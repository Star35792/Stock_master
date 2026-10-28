"""Export current full-universe research results without opening a writer."""

import argparse
import json
import time
from datetime import date
from pathlib import Path

import duckdb

from app.config.settings import settings
from app.repositories.strategy.quality_value_data import QualityValueDataRepository
from app.services.quality_value_data import QualityValueDataService


def wait_for_preparation(path: Path, *, timeout_seconds: int = 7200) -> str:
    """A dependency wait for an already running import, not a scheduled job."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        report = json.loads(path.read_text(encoding='utf-8'))
        status = report.get('status')
        if status in {'success', 'partial', 'failed'}:
            return status
        if status != 'running':
            raise ValueError('unrecognized financial preparation status')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('financial preparation did not finish within timeout')
        time.sleep(min(5, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--after-preparation', type=Path,
                        help='Wait for this running financial import report before reading the DB')
    args = parser.parse_args()
    preparation_status = (
        wait_for_preparation(args.after_preparation) if args.after_preparation else None)
    connection = duckdb.connect(settings.metadata_db_path, read_only=True)
    try:
        connection.execute('BEGIN TRANSACTION')
        service = QualityValueDataService(QualityValueDataRepository(connection))
        result = service.run(as_of=args.as_of)
        result['preparation_status'] = preparation_status
        connection.execute('COMMIT')
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                           encoding='utf-8')
    summary = [
        '# 双低一高：真实数据选股报告', '',
        f"研究日期：{args.as_of}；行情交易日：{result['target_trade_date']}。", '',
        f"全股票池 {result['universe_count']} 只，可信可用行情 "
        f"{result['quote_usable_count']} 只，具备三年年度三表键 "
        f"{result['three_year_statement_set_count']} 只。", '',
        f"状态统计：{result['status_counts']}。", '',
        '正式入选：' + ('、'.join(result['selected_symbols']) or '0只'), '',
        '零入选主要反映证据尚未齐全，不能据此判断市场没有机会。'
        '本报告未认证历史PIT，不代表买入建议或已完成收益验证。', '',
        '## market估值记录覆盖（仅诊断，不作准入）', '',
        '| 来源 | 截止交易日 | 覆盖证券 |', '|---|---|---:|',
    ]
    summary += [f"| {v['source_name']} | {v['latest_trade_date']} | {v['symbols']} |"
                for v in result['valuation_coverage']]
    summary += ['', '## 缺失策略字段', '', '| 字段 | 缺失证券数 |', '|---|---:|']
    summary += [f'| {key} | {value} |' for key, value in result['missing_metric_counts'].items()]
    summary += ['', '## 补证工作队列（不是正式候选或买入排序）', '',
                '仅按已有证据数量、代码排序；未通过估值、低关注及完整质量条件。', '',
                '| 代码 | 名称 | 行业 | 三年累计现金流/利润 | 缺失指标数 |',
                '|---|---|---|---:|---:|']
    summary += [f"| {r['symbol']} | {r['name']} | {r['industry']} | "
                f"{r['ocf_profit_3y']:.3f} | {r['missing_metric_count']} |"
                for r in result['evidence_review_queue']]
    summary += ['', '## 当前行情不可用证券', '',
                '、'.join(result['missing_quote_symbols']) or '无', '',
                '## 后续补证顺序', '',
                '1. 核对新采估值的可信发布与口径、历史窗口和行业比较。',
                '2. 补三年完整年报与可取得季报；缺扣非、商誉、有息债务时继续阻断。',
                '3. 补连续分红、质押、研报覆盖及机构持仓证据。',
                '4. 逐股审查行业模型、归因、催化剂和证伪条件，再复跑正式名单。', '',
                f'逐股证据与阻断明细：{args.output.name}', '']
    args.output.with_suffix('.md').write_text('\n'.join(summary), encoding='utf-8')
    print(json.dumps({key: result[key] for key in (
        'strategy', 'target_trade_date', 'universe_count', 'status_counts',
        'quote_usable_count', 'three_year_statement_set_count', 'selected_symbols',
    )}, default=str))


if __name__ == '__main__':
    main()
