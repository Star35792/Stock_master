# 双低一高研究选股策略 v1

## 状态与边界

策略标识 `quality_value_attention_v1`。规则已实现为纯 Decimal 筛选内核，CLI 接受人工整理证据包；另有只读数据库入口导出全股票池、可计算证据和缺口。没有页面按钮、收益回测或交易信号；不能把样例理解为真实选股结果。数据不全时正式名单允许为空。

适用稳定盈利的非金融企业。金融与强周期模型明确 blocked，不能通过手工改行业标签绕过模型适配。首版保留原始 PE/PB 双低和连续分红约束，是偏成熟分红企业的策略；轻资产、金融、周期中枢估值另做版本，不默默改变口径。参数尚未经样本外验证，不承诺年化 15%–25%。

入口：`backend/app/strategies/quality_value_attention.py`；命令入口：`backend/scripts/research/screen_quality_value_attention.py`。

在 backend 目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.research.screen_quality_value_attention scripts/research/examples/quality_value_attention.json
```

输出到 stdout，重定向即可保存。输入输出不写数据库，不读取外部接口。样例全是虚构证券及测试假设。

真实数据库筛选（数据库未被其他写进程占用时）：

```powershell
.\.venv\Scripts\python.exe -m scripts.research.screen_quality_value_database --as-of 2026-10-28 --output outputs/quality_value_selection_20261028.json
```

该命令以只读事务获取一致快照，同时生成JSON明细和同名Markdown摘要。所有股票均计入状态统计，不用inner join静默丢弃缺行情证券。目标行情日和研究日期分别展示，估值覆盖独立披露；过期估值不映射为当前证据。

可加 `--after-preparation outputs/quality_value_financial_prep.json` 等待已启动的财务补采结束再筛选，最长等待2小时；这是本次流水线依赖等待，不创建定时任务。采集partial/failed不会被解释为数据齐全，状态保留到选股JSON，仍对缺项阻断。补证工作队列只按已有证据完整度/代码排序，未通过估值与低关注检查，不能当作正式候选或买入排序。

自动适配不臆测稳定盈利模型、A/B归因、买入理由或证伪条件：model=`unclassified`、attribution=`unknown`、理由为空时blocked；金融/周期仍需独立模板。当前上市天数与完整20个共同交易日的日均成交额可自动映射；准确三年累计净利润/现金流比、TTM现金流仅在完整且无冲突的同期可信财报满足契约后映射。单期ROE、总负债、分红加利息合计、未拆受限资金等不得换名通过质量门槛。

财务补采可独立执行：`python -m scripts.ingest.prepare_quality_value_financials --all-market --workers 4 --skip-complete --output outputs/quality_value_financial_prep.json`。必须显式指定全市场或证券；网络并行缓存、主线程持锁串行入库。年度4期、季度7期，逐阶段核对完整期末集合，部分返回记incomplete，不请求不可用季度指标来影响已取得报表。输入来源不可用、最近季报尚未披露或源覆盖不足都不能伪装补齐。

现金利润适配只使用同证券CNY报表的 `income.net_profit` 与 `cash_flow.act_cash_flow_net`，三个年度固定为研究日前三个完整自然年；TTM为上年全年加本年最新累计减去年同累计。研究日到4月30日/8月31日/10月31日起，至少要求本年Q1/Q2/Q3累计现金流，缺期不得用旧年报冒充TTM；只有4月30日前且年度现金流已真实可得、没有更新累计期时才允许年度回退。缺任一可比期或同日期同类数据冲突时不映射。known_on取所有贡献记录公告/首次采集/更新的最晚上海日期；未知或未来时间不认证。财报年期键覆盖不等于指标完整或质量通过。

财务补采前若旧库遗失了ORM既有财务唯一索引，可运行 `python -m scripts.maintenance.restore_financial_unique_indexes` 审计，再加 `--apply` 修复。只恢复 financial_statement/financial_indicator 的既有唯一索引；先检查重复/空键，事务内修复并保存前后审计，拒绝自动去重或修改其他表。独立写进程须先确认应用未持库，并使用共享写锁。

## 输入证据合同

请求包含 `as_of`（研究日收盘后）、`candidates`、`pause_new_entries`（默认 false）。证券包含 symbol、industry（使用统一行业代码，去首尾空格）、model、attribution、thesis、invalidation、metrics 和 catalysts。symbol 去首尾空格转大写，重复证券拒绝整个请求。

每个指标为 `{value, source_ref, known_on, valid_through, trusted}`：

- value 必须有限 Decimal；比例与分位都用 0–1，金额用人民币元，不用万元/亿元。
- source_ref 指向可信 market/factor 记录或人工复核证据及版本，不接受供应商 raw 直接冒充可信事实。合并判断需指向可复查的明细清单。
- known_on 是该版本真实首次可用日期，不能写报告期末日期；valid_through 是复核有效期。必须 `known_on <= as_of <= valid_through`，且 trusted=true。
- 数据整理者负责证据真实性、单位、行业模型、历史窗口覆盖和到期规则。程序仅检查输入声明，不能把 trusted=true 理解成系统认证。不同证据可有不同有效期；新公告或重大事件出现时应立即撤销旧有效性。
- 日期粒度只支持收盘后研究，不支持盘中决策；同日公告在收盘后才可使用。没有首次可得/修订链的历史数据不得因补填日期而获得认证。

结果始终 `research_only=true`、`historical_pit_certified=false`、`backtest_eligible=false`，保留证据、催化剂和输入 SHA256。历史财务修订、ST/行业时点、退市样本与公司行为缺口仍然阻断收益认证。

## 四层漏斗

按硬筛、质量、归因、催化剂、关注度及评分依次运行。每层已知失败优先排除，同层未知项也保留在理由中；未知则 blocked。前层不通过不继续后层评分，不代表后层已通过。

### 硬筛

| 字段 | 定义与门槛 |
|---|---|
| market_cap | 总市值 ≥80亿元；≥300亿元为白马档 |
| listed_days | 上市满730自然日 |
| pe_positive / pb_positive | 同一版本扣非正盈利 PE、正净资产 PB 有效且为正；0/1声明 |
| pe_history_percentile / pb_history_percentile | 同口径自身历史估值分位，分别≤0.20；负利润、负净资产时期不能当作便宜样本 |
| valuation_history_years | 有效同口径历史覆盖≥3年，优先完整5年窗口 |
| pe_industry_ratio / pb_industry_ratio | 当前估值/同日可比行业中位数，分别<1；两边均为有效正值 |
| dividend_yield | 过去12个月普通现金股息/当前价格；白马≥0.03，其余≥0.02，排除特别分红造成的不可持续高息 |
| dividend_years | 最近3个完整年度连续现金分红，≥3 |
| amount_mean_20 | 最近20个交易日日均成交额≥5000万元 |
| risk_clear | 完成下列逐项排雷且均无风险才填1；任一命中填0，未核完不提供值 |

risk_clear 明细必须覆盖 ST/*ST/退市风险、过去一年立案调查、审计非标、连续两年亏损、违规担保、商誉/净资产>30%、连续多季负经营现金流、大股东高质押且接近平仓、连续下调指引、非经常损益撑利润、严重产能过剩且无出清迹象。需保存检查期间、证据与复核者；不是自动化审计。任何风险不得靠高总分覆盖。

### 质量

| 字段 | 定义与门槛 |
|---|---|
| roe_mean_3y | 三个完整年度加权平均ROE的算术平均≥0.12；复核杠杆、净资产收缩因素 |
| net_income_sum_3y | 三年累计同口径净利润>0，作为现金流比值有效分母 |
| ocf_profit_3y | 三年累计经营现金流/同期累计净利润≥0.8，不是三个年度比值的平均 |
| ocf_ttm / deducted_profit_ttm | 同期TTM经营现金流、扣非净利润分别>0 |
| deducted_profit_trend_clear | 最近4个单季度未连续两季扣非同比下滑>30%，并检查TTM无持续恶化；通过=1 |
| goodwill_equity | 商誉/正净资产<0.15 |
| receivables_quality_clear | 应收及相关票据/收入、周转天数、账龄与坏账计提未异常恶化；通过=1，不使用增长率1.3倍公式 |
| interest_debt_assets | 有息负债/总资产<0.40 |
| unrestricted_cash_short_debt | 非受限现金/一年内到期有息债务≥1；无短债时录入2作为本策略评分封顶值并在来源中说明 |
| controller_pledge | 控股股东质押股数/其持股数<0.30 |
| pledge_safety_clear | 无质押，或核实存在平仓安全垫；通过=1；未知平仓线不能猜测通过 |
| moat_stable | 市占率与可比毛利率无趋势恶化，有同行/产品结构证据；通过=1 |
| dividend_coverage_clear | 分红获正常化现金流覆盖，考虑必要资本开支，无借债撑分红；通过=1 |

本版不设龙头10% ROE例外，避免主观标签绕过12%门槛。

### 归因与催化剂

attribution=A增速换挡、B风格弃子、C一次性污点、D逻辑损坏。A/B进入下一层；D排除；C额外要求 `one_off_confirmed=1` 的后续财报证据，否则观察。所有0/1字段只接受0或1。

每条催化剂包含 kind、evidence、verify_by、thesis、invalidation。kind 支持资本回报、可验证订单、行业复苏、盈利预测上修。evidence.value=1代表证据已核实而非已经完成未来事件；公告愿望不算执行证据。验证日期必须晚于研究日且不超过183天，证据仍须在研究日有效。无有效催化剂仅观察。多个催化剂不叠加评分。

### 低关注与评分

`coverage_percentile`=过去180天独立研报覆盖机构数的分位，`institution_percentile`=最新已披露机构持仓比例分位，`turnover_percentile`=过去60日平均换手率分位。均在同日同行业、相近市值分组比较；机构持仓使用真实披露日。三项缺任一即blocked，均值>0.40则排除。分组方案与原始值写入来源版本，不能拿全市场小盘和大盘简单比较。

六维总分100；定义 clip(x)=max(0,min(1,x))。未经分数四舍五入直接判断≥70，≥80为阅读优先级，不设另一套准入。公式固定，不随候选池规模漂移：

| 维度 | 公式 |
|---|---|
| 估值25 | 25×[1−(PE分位+PB分位)/2] |
| 质量25 | 25×[clip(ROE/0.20)+clip(三年现金流比/1.2)]/2 |
| 负债表15 | 15×[clip(1−有息负债率/0.4)+clip(1−商誉比例/0.15)+clip(现金短债比/2)]/3 |
| 股东回报10 | 10×clip(股息率/0.05) |
| 低关注10 | 10×(1−三项关注分位均值) |
| 催化剂15 | 最近验证日期≤92天得15分；93–183天得10分 |

## 结果与组合边界

blocked=证据不足或模型不适用；excluded=已知门槛失败；watch=归因待确认、无催化剂、低于70分或排序未选中；selected=研究入选。结果提供 stage、reasons、score、components、thesis、invalidation 及证据。

达到70分后按分数降序、证券代码升序排序，同一行业最多2只、总计最多8只；不足不凑数，暂停新增时候选全部转观察。至少5只且3个行业时 diversification_ready=true，仅表示数量与行业数满足要求。

没有输出仓位，不能把行业最多2只视为已实现行业资金≤30%。后续组合层仍需独立执行单只初始≤8%/最终≤15%、行业≤30%、总仓≤80%、共同风险与风险预算。名单缺证据不是卖出信号；本版不管理已有持仓、不生成清仓或加仓目标。

持仓证伪、价格止损、分批交易与退出优先级留在后续持仓研究版本。市场缩量不自动判休眠；pause_new_entries 为显式人工风险判断，需独立记录理由。

## 验证与后续

先验证单股门槛、未来/过期/未知证据、C/D归因、催化剂期限、确定排序、行业约束和空池。再补可信数据适配，分别比较低估值、质量过滤、低关注、催化剂的增量贡献；完成首次可得/修订链与可交易性门槛前不开展认证收益回测。

T2评审：架构、产品、金融、规则四角色独立审查。采纳独立只读内核、未知阻断、行业规范化、证据声明与系统认证区分、版本合同与测试；拒绝把现有通用多因子排序冒充完整策略。未引入API/schema/存储改动，既有工作区修改原样保留。

首版证据包验证：新增策略测试与既有策略模板测试合计52项通过，Ruff检查通过；包含CLI真实子进程读取虚构样例、输出四类结果和Windows重定向UTF-8验证。四角色提出的行业空格、同层失败优先、人工证据语义及日粒度边界已修正或明确。后续数据库适配验证见下段；投资效果与全字段可信资格不因测试通过而解锁。

数据库适配补充验收（2026-10-28）：策略、数据导出、精确财务、采集、索引修复、既有财报采集/版本链及模板共104项测试通过；长任务依赖的OS写锁另8项通过。四角色复审已关闭TTM年度回退新鲜度P1；只提交本任务文件/文档片段及必要写锁依赖，不夹带其他未提交修复。已完成日线到10月27日、供应商标日估值5577只raw补采；全量财报补采及结束后自动复跑为已启动的当前进程，未声称全字段齐备或产生正式买入名单。
