# 策略优化建议：基于订阅知识库因子池与项目现状的差距分析

> 日期：2026-10-13
> 输入：ima 订阅知识库「股票量化因子股票AI决策引擎」（myhhub/stock）、「A股证券知识库(每日更新)」
> 对照：`aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/hard-gates-execution-plan.md`、`backend/app/strategies/templates.py`、`backend/app/portfolio/builder.py`
> 定位：本文是诊断与建议，不改变任何策略口径、权重或代码行为。涉及因子口径的建议按T2 处理，需决策评审后才能落地。

---

## 1. 先说结论

知识库的因子池**不是你的策略缺口**，你的缺口在别处。

- 项目已有 82 条规范因子定义、11 个因子域、完整的 `category` 分类体系；
- 知识库提供 2200+ 字段，但约 65% 是 MACD/KDJ/RSI/BOLL/K 线形态的同源变体，信息比极低；
- 真正有价值的是知识库**没给你的东西**：A 股事件类因子（龙虎榜席位、游资、涨停封单结构、股东户数、机构调研、限售解禁、股权质押）和资金结构类因子（融资融券 20+ 字段、北向持股、DDX 主力强度）。

**优化方向不是"引入更多因子"，而是把已有 82 条因子跑出有效性证据，并把事件/资金两层接进可信数据层。**

---

## 2. 知识库能补什么：项目当前的真实空白

### 2.1 事件驱动层（最大缺口）

项目 `decision-factor-framework.md` 的 sentiment 域有涨停强度、龙虎榜净买入，但没有席位结构和事件时效。知识库提供了项目完全没有的字段：

| 字段族 | 代表字段 | 项目的状态 |
| --- | --- | --- |
| 龙虎榜席位 | 游资买入净额、游资净买额占比、龙虎榜近一周累计净买入 | 仅有当日净买入额 |
| 涨停结构 | 首板/连板/一字/T字/回封/地天板/天地天、封单金额、封成比、炸板率 | 仅有涨停强度、连板高度 |
| 股东结构 | A股股东户数增长率、户均持股数季度/半年增长率、十大流通股东持股比例 | 未实现 |
| 机构行为 | 近3月机构调研次数、基金/社保/保险/QFII 持股家数、机构评级 | 未实现 |
| 公告事件 | 限售解禁、定向增发、资产重组、股权质押、业绩预告、商誉占净资产比 | 未实现 |
| 融资融券 | 融资余额/买入额/净买入/偿还额、融券余量、融资融券差值 | 仅有 `margin_financing_change_20` 代理 |

其中**股东户数增长率**和**机构调研次数**是你策略文档里已经点名要用的（成长型策略"小盘股需筛选股东户数连续 3 个月下降"），但项目里一个都没有。

### 2.2 资金流层

知识库的 DDX（主力强度）、开盘/收盘抢筹幅度与占比、集合竞价委比/量比、超大单/大单/中单/小单净额，是项目 `capital_flow` 域"代理变量"问题的潜在解药——但要注意这些字段在知识库里**同样是第三方供应商口径**，不是你的可信层，仍然要 `raw -> market`。

### 2.3 估值层的一个具体增量

知识库有 `扣非净利润`、`市盈率TTM(扣非)`、`每股经营现金流`、`每股企业自由现金流量`、`现金分红比例`、`商誉占净资产比例`。

项目的估值因子目前只有 `earnings_yield` / `book_to_price` / `fcf_yield` / `ev_to_ebitda`。**缺"扣非口径"是一个实质漏洞**：项目策略文档强调"不能用非经常性损益粉饰的利润判断质量"，但因子层没有扣非净利润这个入口。

> **已关闭（2026-10-20）**：在 `backend/app/services/valuation_factors.py` 新增 `deducted_earnings_yield` 与 `deducted_earnings_quality`，并补充 `_DEDUCTED_NET_PROFIT` 字段别名与 fail-closed 缺失处理。详见 `aiDoc/research/20261020-2222-deducted-earnings-factor-optimization.md`。

---

## 3. 知识库不能直接用的地方（重要）

### 3.1 因子分类大面积错配，禁止整表导入

如果把 `因子池因子字典.xlsx` 整表灌进 `factor.factor_definition`，会直接污染你已有的 `category` 体系。实测错配：

**被错标为「流动性因子」（24 个）** —— 实际是技术指标：

- `MTM买入信号`、`OBV`、`BR值`、`AR值`、`CR`、`多空线`
- `CCI值`、`CCI超买`、`CCI超卖`、`WR1值`、`WR2值`
- `BIAS1`、`BIAS2`、`BIAS3`、`DPO值`、`MADPO值`、`BBI数值`
- `BBI金叉/死叉`、`DPO金叉/死叉`、`多空线金叉/死叉`
- `神奇九转`、`低点搜寻`、`分离`

**被错标为「波动率因子」（6 个）** —— 实际分属三类：

- `每股股利(税前)` → 分红/估值
- `质押比例`、`限制减持` → 事件风险
- `冲涨停`、`冲跌停` → 动量
- `竞价急速上涨/下跌` → 情绪

**其他错配**：

- `净资产收益率(ROE)`、`投入资本回报率(ROIC)`、`每股留存收益` 被标为**动量因子**，应为质量因子；
- `BOLL(upper值)` 被标为**估值因子**，应为技术面；
- `破净`、`破发`、`总市值` 归类混乱。

**结论**：知识库的分类不可作为分类真源。你的 `FactorCategory` 枚举（`backend/app/factors/categories.py`）是唯一真源。若要接入，只能走"字段名映射表 → 项目 category"的显式映射，且每条映射要单测覆盖。

### 3.2 高度共线因子群

MACD 有 8 个字段（金叉/死叉/顶背离/底背离/值/DIF/DEA/零上金叉/即将金叉），KDJ 有 6 个，均线有 9 个，RSI 有 7 个。同一信息源派生出的多个信号在横截面上 IC 几乎相同。

把它们当独立因子加权，等于给同一个信号加权 N 次。知识库《因子选择指南》给的"趋势型策略 MACD 25% + KDJ + RSI 共振"就是典型例子——看起来是三个条件独立验证，实际是**同一个价格序列的三个变换**，一旦价格动量反转，三者同时失效，形成的是集中亏损而非分散。

**建议**：按信息源而非字段名分组。同源组内先合成一个"族得分"（等权或IC加权），再参与总打分。项目已有 `factor_cross_section` 的 `xxx__rank` / `xxx__zscore` 派生能力，可以直接支撑这个做法。

### 3.3 知识库的"盘后可用"不构成时点正确性

知识库多处标注"日度更新，盘后1小时内可用""财报发布后1周内更新"。这与你项目 `AGENT.MD` 要求的"公告日/首次可得时间 + 修订版本"是两套口径。

- "财报发布后 1 周内更新"比你的规则宽松得多，会引入 1 周的前视窗口；
- 知识库**没有任何字段标注首次可得时间、采集时间、修订版本**；
- 龙虎榜、股东户数、机构持股这类数据的实际披露时间和数据库标注时间可能不同。

**结论**：知识库的"更新频率"字段不能直接写进 `factor.factor_definition` 的时点元数据。需要单独建立 `announced_at` / `available_at` / `revised_at` 三元组，否则整个因子层的时点治理失效。

---

## 4. 项目自身策略层的三个具体优化点

这部分与知识库无关，是读代码时发现的实际问题。

> **实施状态（2026-10-13）**：三项均已实现并通过测试，默认参数保持原有行为，历史研究结果可复现。实现细节见本文第 7 节。

### 4.1 `CrossSectionSelectionStrategy` 强制满仓，与你的价格策略理念冲突

`backend/app/strategies/templates.py:75`：

```python
weight = Decimal(1) / Decimal(len(selected)) if selected else Decimal()
```

只要选出 `top_n` 只股票就**等权分满100%**，剩下的 5% 现金保留完全由下游 `PortfolioBuilder` 事后缩放完成。这带来两个问题：

1. **信号层丢失了强度信息**。第 1 名和第 20 名权重相同。一个"只有 3 只股票合理"的策略和一个"20 只都还行"的策略在信号层无法区分。
2. **与价格与价值匹配策略直接矛盾**。你的策略文档明确要求"无合格候选时持有现金""候选不足时保留未分配预算，不为了凑满 Top N 而降低要求"。当前模板做不到——`selected` 永远等于 `top_n`（只要候选够）。

**建议**：让 `target_weight` 支持"低于 1 的总权重"和分数加权两种模式，由策略模板显式声明 `weighting_mode`，而不是让 `PortfolioBuilder` 反向猜。

### 4.2 `_percentile_ranks` 对缺失值零容忍，会静默缩小股票池

`templates.py:63-67`：

```python
complete_scores = {
    symbol: score / total_weight
    for symbol, score in scores_by_date[signal_date].items()
    if coverage_by_date[signal_date][symbol] == factor_count
}
```

要求某只股票在**全部**因子上的覆盖数等于 `factor_count` 才计分。这意味着只要有一个因子缺失，这只股票就完全消失。

你项目的数据实况是：因子批量管道刚建立，`factor_value` 覆盖率极低。用这个模板做全市场选股，结果会是"股票池莫名其妙很小"，而且 `previous_symbols` 逻辑会把掉出来的股票全部清仓。

**建议**：改为"覆盖率阈值"（如 ≥ 80%）+ 对缺失因子做域内中性化处理，并在报告里显式展示每期实际参与评分的股票数。当前实现下这个数字是不可见的。

### 4.3 `PortfolioBuilder` 的行业约束用等比缩放，会破坏相对权重排序

`builder.py:82-89` 对行业超限做组内等比 `scale`。这在数学上是合理的，但：

- 缩放**只作用于权重，不重新检查单股上限**。如果某行业缩放后仍与其他约束冲突，不做迭代；
- 不支持"优先砍低分股票"的择优截断。等比缩放意味着**砍掉的是该行业内评分最低的股票的一部分权重，但保留了它**；
- 没有风格暴露、没有跟踪误差、没有组合波动率约束（`hard-gates-execution-plan.md` 硬门槛三全部未实现）。

**建议**：加一个可选的择优截断模式——行业超限时按分数从低到高砍到满足约束，而不是等比缩放。等比缩放适合"整体降仓"，择优截断适合"换掉差股票"。

---

## 5. 建议的落地顺序

按你项目的硬门槛规则排序，不跳阶段：

| 优先级 | 动作 | 依据 |
| --- | --- | --- |
| **P0** | 修正 `templates.py` 覆盖率逻辑与权重模式 | 现有代码问题，不改会静默产生错误信号 |
| **P0** | 补 `templates.py` 的目标权重下调能力 + `PortfolioBuilder` 择优截断 | 让策略能表达"持币"和"换股" |
| **P0** | 系统化因子检验：批量算 IC/Rank IC/分层/换手，落 `factor_profile` | `factor_profile`仅 13 条，82 条因子无一条有有效性证据 |
| **P1** | 接事件类数据：龙虎榜席位、涨停结构、股东户数 | 项目完全空白，且知识库字段定义清晰可落 |
| **P1** | 财务因子升级到公告日口径，补扣非净利润因子 | 硬门槛四；缺扣非是实质漏洞 |
| **P1** | 接融资融券与北向真实数据源 | 替换现有代理因子 |
| **P2** | 因子按信息源分组，做族内合成 | 等共线性问题暴露后再做，需 IC 证据支撑 |
| **P2** | 组合风险预算：风格暴露、跟踪误差、波动率目标 | 硬门槛三 |

**明确不建议做的事**：

- 不要把2200+ 字段整表导入；
- 不要因为知识库有某个因子就在策略里直接用——先跑 IC；
- 不要用知识库给的年化收益目标（价值型 15-20%、成长型 25-35%）作为策略验收标准。`hard-gates-execution-plan.md` 明确"权重需通过滚动样本外回测验证，不能根据全样本最优结果反推"，预设收益目标是典型的过度拟合入口。

---

## 6. 关于知识库指南里几条规则的态度

知识库《量化策略开发因子选择指南》有几条方法论是对的，值得吸收：

- **"用行业分位数替代绝对阈值"** —— 与你项目 `decision-factor-framework.md` 第 6 节"财务、估值因子做行业中性化"完全一致，应作为默认口径。
- **"因子拥挤度 > 80 时降低权重"** —— 你项目有 `crowding_score` 和 `sentiment_temperature_percentile`，但只用于打分，未用于权重调节。这是现成能力未消费。
- **"单一因子暴露 ≤ 组合 20%"** —— 对应硬门槛三的风格暴露约束，可直接作为验收指标。
- **"分层回测第一层与第五层年化差 > 8%"** —— 可作为因子失效的量化阈值，写进 `factor_profile` 的告警规则。

需要警惕的：

- **"月度迭代单次调权 ≤ 5%"** —— 人工微调权重无法留痕、无法复现，且与"权重需通过滚动样本外回测验证"冲突。应该是重跑 walk-forward，而不是手调。
- **"每年重构因子组合应对风格切换"** —— 对应硬门槛二的滚动窗口，当前 `rolling` 执行是 fail-closed 状态，不能宣称已完成。

---

## 关联文档

- `aiDoc/research/decision-factor-framework.md`：因子域设计与分类真源
- `aiDoc/plans/hard-gates-execution-plan.md`：四项金融正确性硬门槛
- `aiDoc/strategy/20260922-1314-price-value-margin-strategy.md`：价格与价值匹配策略
- `backend/app/strategies/templates.py`：横截面选股模板
- `backend/app/portfolio/builder.py`：组合构建器
- `backend/app/factors/categories.py`：因子分类枚举（唯一真源）

---

## 7. 已实施：策略层资金表达与约束执行（2026-10-13）

按第 5 节 P0 顺序实施，并在 T2 提交前四角色评审后关闭 P1 缺口。范围限定在策略层、组合构建层与因子选股研究入口，不改变因子口径、数据契约、回测引擎或风险政策。

### 7.1 策略层：覆盖率阈值、持币与分数加权

`backend/app/strategies/templates.py`、`backend/app/strategies/inputs.py`

- 2026-10-28 修复横截面排名并列值口径：策略模板复用 `app/factors/rank_ic.py::rank_values` 的 Decimal 平均秩；同值证券共享分数，单标的与全体同值取中性 `0.5`，反向因子使用 `1-rank`。Top-N 容量切穿同分组时仍以证券代码稳定决胜，但代码不再进入因子分数或分数加权。旧研究运行不重写，新运行必须按代码版本区分。
- 2026-10-28 将同一口径贯通到持久化组合因子：`CompositeFactorService` 不再用证券代码拆分并列值，改为复用同一 Decimal 平均秩；无横截面离散度时输出中性 `0.5`。默认 `decision_score` 版本由 `v1` 升为 `v2-average-rank`，CLI 同步使用新默认，避免新算法静默覆盖旧版落盘值；既有 `v1` 只保留为历史证据，服务入口拒绝以当前代码重算该冻结版本。

- 新增 `WeightingMode`：`EQUAL_WEIGHT_FULLY_INVESTED`（默认，保持原行为，**始终满仓等权**，不读取 `invested_weight` 表达的部分仓位语义）、`EQUAL_WEIGHT_PARTIAL`（入选集等权，但总仓位为 `invested_weight`，剩余持币）、`SCORE_WEIGHTED`（按归一化分数加权）。
- 新增 `MinimumScorePolicy`：`ALWAYS_FULLY_INVESTED`（默认）、`HOLD_CASH_WHEN_NO_CANDIDATE`。
- 新增 `coverage_ratio` 参数（默认 `None`，等价于 1.0）：因子覆盖数 / 因子总数低于阈值的证券不参与评分，不再要求全因子齐备。
- 新增 `invested_weight`（默认 1.0）：配合 `HOLD_CASH_WHEN_NO_CANDIDATE` 可表达"只投 60%，其余持币"。与 `EQUAL_WEIGHT_FULLY_INVESTED` 组合时会被拒绝，因为该模式语义上恒满仓。
- 新增 `minimum_score`（默认 `None`）：**覆盖率过滤之后、top_n 截断之前** 的分数门槛。低于门槛的证券被剔除，`below_minimum_score_count` 记录剔除数；入选集在门槛之上按分数降序取 `top_n`，权重按 `invested_weight` 分配，不再为凑满而降低要求。
- 新增 `CrossSectionDecision` 返回结构，通过 `decide` / `decide_history` 暴露。字段语义：
  - 计数类 `eligible_symbols`、`scored_symbols`、`selected_symbols`、`dropped_for_coverage`、`below_minimum_score_count` 跨决策日求和；
  - `distinct_selected_symbols` 是全期唯一集合语义（同一标的多日入选只计一次）；
  - `invested_weight`、`coverage_threshold`、`weighting_mode`、`minimum_score_policy`、`signal_date` 取最后一个决策日的值。
- `generate` / `generate_history` 签名与返回类型不变；**同一决策日内的正权信号按字母序发射**，保持向后兼容。
- 当无任何证券通过覆盖率或分数门槛时，输出全部清零信号且 `invested_weight=0`，即持币。

守卫与精度：
- `SCORE_WEIGHTED` 对入选集逐项校验：非有限分数、负分、合计非正均 fail-closed 抛 `ValueError`——负权重隐含裸空头，A 股个股不可交易。
- `StrategyFactorFrame` 在构造期拒绝非有限因子值。
- 等权分配出现除不尽时（如 1/3），将残差归集到第一个符号，保证 `sum(weights) == invested_weight` 恒成立，避免 `invested_weight` 报值失真。
- `minimum_score` 为负被拒绝；`HOLD_CASH_WHEN_NO_CANDIDATE` 配合满仓等权模式且 `invested_weight != 1` 被拒绝。

### 7.2 组合层：择优截断与单标的额外上限

`backend/app/portfolio/builder.py`、`backend/app/portfolio/enums.py`

- 新增 `ConstraintEnforcementMode`（独立置于 `app/portfolio/enums.py`，schema 与领域实现共用，避免 schema 反向依赖领域模块）：`PROPORTIONAL_SCALE`（默认，等比缩放，保持原行为）、`SCORE_RANKED_TRIM`。
- 新增 `PortfolioAsset.score`（可选 `Decimal`）：分组预算超限时，择优截断模式按分数升序削减，最高分保留完整请求权重，被削到 0 的标的离场并记录 `trimmed_to_zero`。
- `SCORE_RANKED_TRIM` 要求每个资产都有 `score`，缺失或非有限分数时 fail-closed 抛错，不猜测排序。
- 新增 `PortfolioConstraints.extra_symbol_caps`：个别标的比通用 `max_symbol_weight` 更严时使用。它与通用上限在构造期通过 `min(max_symbol_weight, extra_symbol_caps.get(symbol, 1))` 合成单一有效上限，请求权重在构造期即被该上限封顶；由于 `_enforce_group` 的等比缩放与择优截断都是单调不增操作，后续组约束只会让权重持平或下降，因此不存在也不需要"组约束执行后再回补导致超限"的情况（原实现里的 `symbol_cap_reapplied` 二次校验是死代码，已删除）。
- 现金保留缩放跳过权重已为 0 的资产，避免在退出标的上产生无意义的 `cash_reserve` 噪音。
- 当 `constrained_weight > 0` 但整手取整后 `target_quantity == 0` 时，记录 `budget_unallocated` 原因，避免预算静默落空。

### 7.3 API 契约与持久化

`backend/app/schemas/portfolio.py`、`backend/app/schemas/strategy.py`、`backend/app/services/portfolio.py`、`backend/app/services/strategy_research.py`、`backend/app/models/portfolio/plan.py`

- `PortfolioAssetRequest` 新增可选 `score`。
- `PortfolioConstraintsRequest` 新增 `enforcement_mode` 与 `extra_symbol_caps`，均为可选且有默认值；`ConstraintEnforcementMode` 来自 `app/portfolio/enums.py`。
- `PortfolioPlanCreateRequest` 增加跨字段校验：`enforcement_mode=score_ranked_trim` 时所有资产必须带 `score`，否则 422。
- 组合计划的 `parameters` 记录 `enforcement_mode` 和 `extra_symbol_caps`；`portfolio_target` 新增 `score` 列，截断顺序与约束口径可回放。
- 因子选股回测请求 `FactorSelectionBacktestRequest` 暴露 `coverage_ratio`、`weighting_mode`、`minimum_score_policy`、`minimum_score`、`invested_weight`；`services/strategy_research.py` 将其传入 `MultiFactorStrategy` 并写入研究合同。
- `services/research_validation.py` 的 `_COMPARABLE_PARAMETER_KEYS` 纳入上述新策略参数，避免跨运行比较时忽略资金表达差异。

### 7.4 验证

- 后端全量测试 583 项通过（`pytest -q`，含 20 条既有告警），`ruff check app/ tests/` 无告警。
- 本次改动相关的测试文件：`test_strategy_templates.py` 10→26、`test_portfolio_builder.py` 3→12、`test_portfolio_service.py` 2→6。
- 覆盖重点：覆盖率阈值过滤、持币无候选、`minimum_score` 先过滤后取 top_n、等权残差归集、`SCORE_WEIGHTED` 负分/非有限 fail-closed、择优截断按升序削减且被削到 0 离场、`budget_unallocated` 审计、`score_ranked_trim` 缺 `score` 的 422 契约、`extra_symbol_caps` 与通用上限的 `min()` 合成、分数持久化与回放、研究 API 参数往返。
- 默认参数下既有断言全部未变，确认向后兼容。

### 7.5 尚未覆盖

本次只改资金表达和约束执行，以下仍在后续切片：

- 因子有效性证据（IC/Rank IC/分层/换手）仍缺失，`factor_profile` 数据不足以支撑权重调整。→ 见第 8 节批量因子截面诊断（2026-10-14）。
- 行业与风格暴露、跟踪误差、组合波动率约束仍未实现（硬门槛三）。
- `SCORE_RANKED_TRIM` 的隐藏换手与往返交易成本需在接入回测后进一步评估；当前未做最小变更阈值或换手约束。

---

## 8. 已实施：批量因子截面诊断（2026-10-14）

落实第 5 节 P0「系统化因子检验：批量算 IC/Rank IC/分层/换手，落 `factor_profile`」的信息侧。范围限定在因子诊断的**运行与聚合入口**，不改变因子口径、截面变换数学、数据契约或策略权重。

### 8.1 服务层

`backend/app/services/factor_batch_profiler.py`、`backend/app/repositories/factor/factor_profile.py`

- 新增 `FactorBatchProfilerService`：从 `factor.factor_definition` 取全部活跃定义，对每个（`name`、`version`、`category`）逐个调用 `FactorCrossSectionService.run`，生成逐日截面的统计分布、IC / Rank IC（1/5/20 日）、分层收益、换手和 winsor/zscore/rank/industry_neutral 派生因子。
- 派生 `__` 变换因子（横截面输出而非源）在候选筛选阶段跳过，避免对变换结果二次变换。
- 失败按因子隔离：单个因子抛错只回滚该因子事务并记录 `error_message`，同批其他因子继续；整批运行写入 `system.job_log`（`status=success/partial/failed`、`rows_failed`、`metadata.failed_factors`）。
- 支持 `categories` 白名单、`excluded_categories` 黑名单，以及 size 因子对（`size_factor_name`/`size_factor_version`）的成对校验；`adjust_type`、日期区间在运行前校验。
- `FactorProfileRepository.summarize` 新增按（`factor_name`、`factor_version`）聚合的方法，只用跨 DuckDB/PostgreSQL 均可移植的 `count/min/max/avg`，不依赖方言特定标准差；`FactorBatchProfilerService.summarize` 映射为 `FactorProfileSummary`，缺失 horizon 返回 `None` 而非 0，`has_ic_evidence` 显式标注是否已有 IC 证据。

### 8.2 API 与命令行

`backend/app/api/routes/factors.py`、`backend/app/schemas/factor.py`、`backend/scripts/factors/run_batch_profiles.py`

- `POST /api/v1/factors/batch-profiles`：触发批量诊断，返回成功/失败计数、profile/派生行数和逐因子结果；日期区间反向返回 422。
- `GET /api/v1/factors/batch-profiles/summary`：按因子聚合已持久化 IC 证据，支持 `factor_name`、`factor_version`、`start_date`、`end_date` 过滤与分页。
- 命令行入口 `scripts/factors/run_batch_profiles.py`：支持 `--categories`、`--excluded-categories`、`--no-size-neutral`，失败因子以非零退出码结束。

### 8.3 验证

- 后端全量测试通过（`pytest -q`），`ruff check app/ tests/ scripts/` 无告警。
- 新增测试：`test_factor_batch_profiler.py` 覆盖候选筛选与派生跳过、类别过滤、失败隔离与 `partial` 状态、日期区间/ size 对校验、summary 映射与无 IC 证据分支，并含一条 DuckDB 真实聚合校验；`test_api.py` 新增批量运行转发、反向区间 422、summary 转发与 `openapi` 路由断言。

### 8.4 尚未覆盖

- 各活跃因子对的历史 IC 证据需要**实际运行**批量诊断并落库后才有数据；本次只交付运行与聚合入口。
- 分层回测年化差、因子拥挤度权重调节、因子失效告警阈值仍未实现；需要接入回测或调度后继续。
- 批量诊断尚未接入 `factor_pipeline` 日级调度；当前由 API 或命令行显式触发。
