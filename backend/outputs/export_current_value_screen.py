"""Export the current immutable screen artifact for human review."""

import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.services.price_value_screen import PriceValueScreenService

engine = create_engine(f'duckdb:///{settings.metadata_db_path}')
with Session(engine) as db:
    report = PriceValueScreenService().latest(db)
engine.dispose()
assert report and report['candidate_eligible'], 'current report not eligible'
by_symbol = {row['symbol']: row for row in report['rows']}
selected = [by_symbol[symbol] for symbol in report['selection']['ordered_symbols']]
assert all(row['shortlist']['selected'] for row in selected)
missing_quality = sum(row['research_diagnostics']['quality']['status'] == 'unknown'
                      for row in report['rows'])
missing_trend = sum(row['research_diagnostics']['trend']['status'] == 'unknown'
                    for row in report['rows'])
stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
base = Path('outputs') / f'price_value_selected_{stamp}'
export = {k: v for k, v in report.items() if k != 'rows'}
export['rows'] = selected
base.with_suffix('.json').write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding='utf-8')
lines = [
    '# 每日初筛：优先研究名单（当前补采观察）', '',
    f"价格日：{report['price_as_of']}；财务可得日：{report['financial_as_of']}；分析时刻：{report['analysis_at']}",
    f"运行：{report['run_id']}；全市场：{report['universe_count']}；基础通过：{report['ready_count']}；"
    f"原走势现金流池：{report['research_candidate_count']}；新增硬条件达标："
    f"{report['selection']['qualified_count']}；优先研究：{len(selected)}。", '',
    '条件：原走势现金流门槛；PE≤20、PB≤2、上市≥730天、可信20日均成交≥5000万元、'
    '三年每年盈利、主报告期及已披露更新期ROE>0、MA20偏离≤8%。周期行业及ROE>30%需单独复核。',
    report['selection']['ranking_description'],
    'PE为供应商未认证周期值；ROE是披露期加权值，非年化。顺序为研究工作安排，非收益预测或买入顺序。',
    f'这是当前研究观察，非当晚历史名单；尚未验证未来收益。剩余财务证据不足{missing_quality}只，走势证据不足{missing_trend}只，均未放行。', '',
    '|池内排序|代码|名称|行业|PE（供应商）|PB|披露期ROE|20日涨幅|三年现金流/利润|',
    '|---:|---|---|---|---:|---:|---:|---:|---:|',
]
for row in selected:
    diag = row['research_diagnostics']
    lines.append(f"|{row['shortlist']['rank']}|{row['symbol']}|{row['name']}|{row['industry']}|"
                 f"{float(row['pe_vendor_unverified']):.2f}|"
                 f"{float(row['pb_vendor']):.2f}|{float(row['roe_reported_vendor'])*100:.2f}%|"
                 f"{float(diag['trend']['return_20'])*100:.2f}%|"
                 f"{float(diag['quality']['ocf_profit_3y']):.2f}|")
base.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'run_id': report['run_id'], 'selected': len(selected),
                  'candidate_eligible': report['candidate_eligible'],
                  'markdown': str(base.with_suffix('.md').resolve()),
                  'json': str(base.with_suffix('.json').resolve())}))
