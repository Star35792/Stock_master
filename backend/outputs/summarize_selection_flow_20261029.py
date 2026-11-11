"""Combine actual outputs without promoting an initial screen to final selection."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

folder = Path('outputs')
initial_path = folder / 'price_value_selected_20261029-162629.json'
initial = json.loads(initial_path.read_text(encoding='utf-8'))
strict = json.loads((folder / 'quality_value_full_flow_20261029.json').read_text(encoding='utf-8'))
strict_by_symbol = {row['symbol']: row for row in strict['rows']}
cohort = Counter(strict_by_symbol[row['symbol']]['status'] for row in initial['rows'])
lines = [
    '# 本次选股流程实际运行结果', '',
    f"研究时刻：{initial['analysis_at']}；价格日：{initial['price_as_of']}。",
    f"每日初筛运行：{initial['run_id']}；初筛版本：{initial['screen_version']}。", '',
    '## 运行范围与结果', '',
    '|步骤|实际结果|', '|---|---|',
    f"|每日初筛：全市场数据与适用性检查|{initial['universe_count']}只中{initial['ready_count']}只通过|",
    f"|每日初筛：完整21日走势与盈利现金流条件|{len(initial['rows'])}只通过，已核验当前报告有效|",
    '|严格价值策略：行业模型、硬门槛、质量、归因、催化剂、评分准入|已实际执行；正式入选0只。5120只阻断于行业模型，465只因上市年限或ST排除|',
    '|价值研究页：逐股FCFE估值及单股假设历史回测|缺少用户声明的逐股预测、股本和历史假设版本，未执行，不生成虚构估值或历史收益|', '',
    '每日初筛和严格价值策略是两个现有入口，表中不是同一条已打通的自动流水线。严格策略自动映射目前仅覆盖上市年限、流动性和部分现金流指标；不能把它的0只入选解读为全部股票已经通过完整估值研究后被淘汰。',
    f'364只初筛股票在严格策略中的状态：{dict(cohort)}。', '',
    '## 本次发现的实际限制', '',
    '- 每日初筛默认没有PE/PB上限或ROE下限，因此高PE股票仍可能通过走势与现金流门槛；364只不是已确认便宜的股票。',
    '- 严格策略的行业适用模型、投资论点、证伪条件及催化剂尚未逐股确认；分红、质押、扣非、估值历史分位和关注度等证据尚未映射齐全。',
    '- 真实采集发生在10月29日，不能将本报告当成10月28日晚间就已可得的历史名单。', '',
    '## 每日初筛实际通过名单', '',
    '按证券代码排列，不代表优先级。PE为供应商周期未认证值；ROE为2026年半年报加权值、非年化。', '',
    '|代码|名称|行业|PE（供应商）|PB|半年ROE|20交易日涨幅|三年经营现金流/利润|',
    '|---|---|---|---:|---:|---:|---:|---:|',
]
for row in initial['rows']:
    diag = row['research_diagnostics']
    lines.append(
        f"|{row['symbol']}|{row['name']}|{row['industry']}|"
        f"{Decimal(row['pe_vendor_unverified']):.2f}|{Decimal(row['pb_vendor']):.2f}|"
        f"{Decimal(row['roe_reported_vendor'])*100:.2f}%|"
        f"{Decimal(diag['trend']['return_20'])*100:.2f}%|"
        f"{Decimal(diag['quality']['ocf_profit_3y']):.2f}|"
    )
output = folder / '20261029-1626-selection-flow-results.md'
output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'report': str(output.resolve()), 'initial_count': len(initial['rows']),
                  'strict_selected': strict['selected_symbols'], 'strict_cohort': dict(cohort)}))
