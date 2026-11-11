from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pytest

from app.repositories.strategy.quality_value_data import QualityValueDataRepository
from app.services.quality_value_data import QualityValueDataService
from scripts.research.screen_quality_value_database import wait_for_preparation

DAY = date(2026, 10, 28)
UPDATED = datetime(2026, 10, 28, tzinfo=UTC)


class Repository:
    def stocks(self):
        return [{
            'symbol': symbol, 'name': symbol, 'exchange': 'SH', 'industry': '测试行业',
            'list_date': date(2010, 1, 1), 'delist_date': None, 'is_active': True,
            'currency': 'CNY', 'source_name': 'fixture', 'updated_at': UPDATED,
        } for symbol in ('A', 'B', 'C')]

    def latest_trade_date(self, *, as_of):
        return date(2026, 10, 27)

    def expected_completed_session(self, *, as_of):
        return date(2026, 10, 27)

    def bars(self, *, trade_date):
        return [{
            'symbol': symbol, 'close': Decimal(10), 'source_name': 'fixture',
            'special_treatment_status': status, 'is_suspended': False,
            'quality_status': 'trusted', 'is_suspect': False, 'updated_at': UPDATED,
        } for symbol, status in [('A', 'normal'), ('B', 'st')]]

    def liquidity(self, *, trade_date):
        return [{
            'symbol': 'A', 'sessions': 20, 'amount_sum': Decimal('1234567890.12'),
            'updated_at': UPDATED,
        }, {
            'symbol': 'B', 'sessions': 19, 'amount_sum': Decimal(1000000000),
            'updated_at': UPDATED,
        }]

    def financial_coverage(self, *, as_of):
        return [{'symbol': 'A', 'annual_keys': 9}]

    def financial_observations(self, *, as_of):
        return []

    def valuation_coverage(self, *, as_of):
        return [{'source_name': 'fixture', 'latest_trade_date': date(2026, 9, 24)}]


def test_real_data_export_keeps_all_stocks_and_does_not_fabricate_evidence():
    result = QualityValueDataService(Repository()).run(as_of=DAY)
    assert result['universe_count'] == sum(result['status_counts'].values()) == 3
    assert result['status_counts'] == {'blocked': 2, 'excluded': 1}
    assert result['selected_symbols'] == []
    assert result['quote_usable_count'] == 2
    assert result['missing_quote_symbols'] == ['C']
    assert result['three_year_statement_set_count'] == 1
    assert result['missing_metric_counts']['risk_clear'] == 3
    request = result['evidence_request']
    assert all(c['attribution'] == 'unknown' and c['thesis'] is None
               for c in request['candidates'])
    metrics = request['candidates'][0]['metrics']
    assert Decimal(metrics['amount_mean_20']['value']) == Decimal('61728394.506')
    assert 'amount_mean_20' not in request['candidates'][1]['metrics']
    assert 'pe_positive' not in metrics  # stale valuation cannot be promoted
    assert 'roe_mean_3y' not in metrics
    assert not result['backtest_eligible']


def test_future_reference_data_cannot_be_exported_as_known():
    result = QualityValueDataService(Repository()).run(as_of=date(2026, 10, 27))
    assert all(not c['metrics'] for c in result['evidence_request']['candidates'])


def test_stale_market_cannot_be_reported_as_current():
    class Stale(Repository):
        def latest_trade_date(self, *, as_of):
            return date(2026, 10, 26)

    result = QualityValueDataService(Stale()).run(as_of=DAY)
    assert not result['market_data_fresh']
    assert result['selected_symbols'] == []
    assert next(r for r in result['rows'] if r['symbol'] == 'A')['stage'] == 'freshness'


def test_evidence_queue_is_separate_from_formal_selection():
    class Financial(Repository):
        def financial_observations(self, *, as_of):
            return [{
                'id': f'{kind}-{year}', 'symbol': 'A', 'statement_type': kind,
                'period_end': date(year, 12, 31), 'report_date': date(year + 1, 4, 1),
                'currency': 'CNY', 'source_name': 'fixture',
                'created_at': UPDATED, 'updated_at': UPDATED,
                'metrics': {'net_profit' if kind == 'income' else 'act_cash_flow_net': '100'},
            } for year in (2023, 2024, 2025) for kind in ('income', 'cash_flow')]

    result = QualityValueDataService(Financial()).run(as_of=DAY)
    assert result['selected_symbols'] == []
    assert result['evidence_review_queue'][0]['symbol'] == 'A'
    assert result['evidence_review_queue'][0]['ocf_profit_3y'] == Decimal(1)
    assert result['evidence_review_queue'][0]['missing_metric_count'] > 0


def test_liquidity_repository_requires_shared_twenty_sessions():
    connection = duckdb.connect()
    connection.execute('CREATE SCHEMA market')
    connection.execute('''CREATE TABLE market.trading_calendar
        (trade_date DATE, exchange VARCHAR, is_open BOOLEAN)''')
    connection.execute('''CREATE TABLE market.daily_bar
        (symbol VARCHAR, trade_date DATE, adjust_type VARCHAR, security_type VARCHAR,
         quality_status VARCHAR, is_suspect BOOLEAN, is_suspended BOOLEAN,
         amount DECIMAL(30,6), source_name VARCHAR, updated_at TIMESTAMPTZ)''')
    connection.execute('''INSERT INTO market.trading_calendar
        SELECT CAST('2026-10-01' AS DATE) + CAST(i AS INTEGER), 'SH', true
        FROM range(21) t(i)''')
    connection.execute('''INSERT INTO market.daily_bar
        SELECT 'A', trade_date, 'none', 'stock', 'trusted', false, false,
               100.123456, 'fixture', now() FROM market.trading_calendar''')
    rows = QualityValueDataRepository(connection).liquidity(trade_date=date(2026, 10, 21))
    assert rows[0]['sessions'] == 20
    assert rows[0]['amount_sum'] == Decimal('2002.469120')
    connection.close()


def test_preparation_wait_does_not_treat_partial_data_as_complete(tmp_path):
    report = tmp_path / 'prep.json'
    report.write_text('{"status":"partial"}', encoding='utf-8')
    assert wait_for_preparation(report, timeout_seconds=0) == 'partial'
    report.write_text('{"status":"running"}', encoding='utf-8')
    with pytest.raises(TimeoutError):
        wait_for_preparation(report, timeout_seconds=0)


def test_financial_sector_does_not_receive_generic_cash_flow_evidence():
    class Bank(Repository):
        def stocks(self):
            return [dict(row, industry='银行') for row in super().stocks()]

    result = QualityValueDataService(Bank()).run(as_of=DAY)
    assert all(c['model'] == 'financial' for c in result['evidence_request']['candidates'])
    assert result['evidence_review_queue'] == []
