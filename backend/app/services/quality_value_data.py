"""Current trusted-data readiness and a full-universe, fail-closed screen."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.repositories.strategy.quality_value_data import QualityValueDataRepository
from app.services.quality_value_financial import financial_metrics
from app.strategies.quality_value_attention import (
    ATTENTION,
    HARD_GATES,
    QUALITY_GATES,
    Candidate,
    Evidence,
    ScreenRequest,
    screen,
)


class QualityValueDataService:
    def __init__(self, repository: QualityValueDataRepository):
        self.repository = repository

    def run(self, *, as_of: date) -> dict:
        repo = self.repository
        trade_date = repo.latest_trade_date(as_of=as_of)
        expected_session = repo.expected_completed_session(as_of=as_of)
        fresh = bool(trade_date and expected_session and trade_date >= expected_session)
        stocks = repo.stocks()
        bars = {r['symbol']: r for r in repo.bars(trade_date=trade_date)} if trade_date else {}
        liquidity = {r['symbol']: r for r in repo.liquidity(trade_date=trade_date)} \
            if trade_date else {}
        financial = {r['symbol']: r for r in repo.financial_coverage(as_of=as_of)}
        financial_rows: dict[str, list[dict]] = defaultdict(list)
        for observation in repo.financial_observations(as_of=as_of):
            financial_rows[observation['symbol']].append(observation)
        required = set(HARD_GATES) | set(QUALITY_GATES) | set(ATTENTION) | {
            'dividend_yield', 'amount_mean_20',
        }
        candidates, readiness = [], []
        for stock in stocks:
            symbol = stock['symbol']
            metrics: dict[str, Evidence] = financial_metrics(financial_rows[symbol], as_of)

            def add(name, value, source, updated, destination=metrics):
                if updated is None or not source:
                    return
                known_on = updated.astimezone(ZoneInfo('Asia/Shanghai')).date()
                if known_on > as_of:
                    return
                destination[name] = Evidence(
                    value=value, source_ref=source, known_on=known_on,
                    valid_through=as_of, trusted=True,
                )

            # Current reference metadata can prove listing age for current
            # research; it does not certify historical ST or industry membership.
            if stock['list_date'] and stock['list_date'] <= as_of:
                add('listed_days', Decimal((as_of - stock['list_date']).days),
                    f"market.security:{symbol}:{stock['source_name']}", stock['updated_at'])
            row = liquidity.get(symbol)
            if row and row['sessions'] == 20:
                add('amount_mean_20', row['amount_sum'] / Decimal(20),
                    f'market.daily_bar:{symbol}:none:20sessions:{trade_date}', row['updated_at'])

            candidate = Candidate(
                symbol=symbol, industry=stock['industry'] or 'unknown',
                model='unclassified', attribution='unknown', metrics=metrics,
            )
            candidates.append(candidate)
            bar = bars.get(symbol)
            quote_usable = bool(
                bar and bar['quality_status'] in {'trusted', 'manual_fixed'}
                and not bar['is_suspect'] and not bar['is_suspended']
                and bar['close'] is not None and bar['close'] > 0 and bar['source_name']
            )
            blockers = ['industry_model_unreviewed', 'attribution_unreviewed',
                        'thesis_and_invalidation_unreviewed', 'catalyst_missing']
            if not quote_usable:
                blockers.append('trusted_current_quote_unavailable')
            if not fresh:
                blockers.append('market_data_stale_or_calendar_missing')
            f = financial.get(symbol, {})
            if f.get('annual_keys', 0) != 9:
                blockers.append('three_annual_statement_sets_incomplete')
            readiness.append({
                'symbol': symbol, 'name': stock['name'], 'exchange': stock['exchange'],
                'industry': stock['industry'], 'quote_usable': quote_usable,
                'financial': f, 'available_metrics': sorted(metrics),
                'missing_metrics': sorted(required - set(metrics)), 'blockers': blockers,
            })
        request = ScreenRequest(as_of=as_of, candidates=candidates)
        result = screen(request)
        # Objective current restrictions can exclude even an otherwise unknown
        # industry model. These checks cannot promote any stock to selected.
        by_symbol = {r['symbol']: r for r in result['rows']}
        for stock in stocks:
            reasons = []
            if not stock['is_active'] or (stock['delist_date'] and stock['delist_date'] <= as_of):
                reasons.append('inactive_or_delisted')
            if stock['list_date'] and (as_of - stock['list_date']).days < 730:
                reasons.append('listed_less_than_two_years')
            bar = bars.get(stock['symbol'])
            # ST status is a current reference observation, never backfilled PIT.
            if bar and bar['special_treatment_status'] in {'st', '*st', 'delisting'}:
                reasons.append('current_special_treatment_restricted')
            if reasons:
                by_symbol[stock['symbol']].update(status='excluded', stage='current_universe',
                                                 reasons=reasons)
        if not fresh:
            for row in result['rows']:
                if row['status'] != 'excluded':
                    row.update(status='blocked', stage='freshness',
                               reasons=['market_data_stale_or_calendar_missing'])
        result['selected_symbols'] = [r['symbol'] for r in result['rows']
                                      if r['status'] == 'selected']
        selected_industries = {r['industry'] for r in result['rows']
                               if r['status'] == 'selected'}
        result['diversification_ready'] = (
            len(result['selected_symbols']) >= 5 and len(selected_industries) >= 3)
        counts = Counter(row['status'] for row in result['rows'])
        missing = Counter(name for row in readiness for name in row['missing_metrics'])
        # An evidence-work queue, not a valuation/attention-qualified shortlist.
        # Sorting only by completeness avoids disguising missing scores as low scores.
        review_queue = []
        candidates_by_symbol = {c.symbol: c for c in candidates}
        for item in readiness:
            candidate = candidates_by_symbol[item['symbol']]
            m = candidate.metrics
            if (by_symbol[item['symbol']]['status'] != 'excluded' and item['quote_usable']
                    and fresh and all(key in m for key in (
                        'listed_days', 'amount_mean_20', 'net_income_sum_3y', 'ocf_profit_3y'))
                    and m['listed_days'].value >= 730
                    and m['amount_mean_20'].value >= Decimal('50000000')
                    and m['net_income_sum_3y'].value > 0
                    and m['ocf_profit_3y'].value >= Decimal('0.8')):
                review_queue.append({
                    'symbol': item['symbol'], 'name': item['name'],
                    'industry': item['industry'], 'available_metric_count': len(m),
                    'ocf_profit_3y': m['ocf_profit_3y'].value,
                    'missing_metric_count': len(item['missing_metrics']),
                })
        review_queue.sort(key=lambda r: (-r['available_metric_count'], r['symbol']))
        result.update(
            data_mode='current_trusted_readiness_v1', target_trade_date=trade_date,
            expected_completed_session=expected_session, market_data_fresh=fresh,
            universe_count=len(stocks), status_counts=dict(counts),
            quote_usable_count=sum(r['quote_usable'] for r in readiness),
            missing_quote_symbols=[r['symbol'] for r in readiness if not r['quote_usable']],
            valuation_coverage=repo.valuation_coverage(as_of=as_of),
            three_year_statement_set_count=sum(
                r['financial'].get('annual_keys', 0) == 9 for r in readiness),
            missing_metric_counts=dict(sorted(missing.items())), readiness=readiness,
            evidence_review_queue=review_queue[:20],
            evidence_request=request.model_dump(mode='json'),
            limitations=[
                'Only listing age, complete 20-session liquidity and exact supported '
                'cash/profit statement metrics are mapped automatically.',
                'No raw, stale valuation, single-period ROE or total-debt proxy is promoted.',
                'Industry model, attribution, thesis, invalidation and catalyst need review.',
                'Zero selected reflects incomplete evidence, not proof of no opportunities.',
            ],
        )
        return result
