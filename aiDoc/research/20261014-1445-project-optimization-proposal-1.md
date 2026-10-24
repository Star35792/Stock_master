# 项目现状优化方案 #1：从「能跑闭环」到「有证据支撑」

> 日期：2026-10-14
> 作者：AI 协作分析
> 定位：基于当前项目现状的下一阶段优先级与执行建议；不改变既有代码行为，仅作为后续切片的决策输入。

---

## 1. 项目现状快照

| 维度 | 当前状态 | 风险/缺口 |
| --- | --- | --- |
| **数据底座 V1-V2** | `raw -> market` 可信日线已跑通；复权因子、停牌、ST 状态字段已落地但**未全量历史回填**。 | 历史 ST、真实停牌事件、涨跌停边界未完全核验；公司行为落地前除权除息区间仍 fail-closed。 |
| **因子工厂 V3** | 82 条活跃因子定义、11 个因子域；横截面诊断（IC/Rank IC/分层/换手）和批量诊断入口已交付。 | `factor_profile` 需要**实际运行一次批量诊断**后才有 IC 证据；事件/资金类因子（龙虎榜席位、股东户数、融资融券、北向）仍为空白。 |
| **研究回测 V4** | 单次/批量/样本外/滚动 walk-forward 可执行；训练产物 `training_artifact` 已不可变绑定。 | 滚动窗口相邻测试期未复合收益；平台内自动训练选参未实现；归因层已具备底座但未做样本外分市场状态分析。 |
| **组合与风控 V5** | 组合草案、风险预算行业上限、事前风控、paper 确认/执行/审计闭环已可用。 | 风格暴露、跟踪误差、组合波动率约束仅暴露 schema 占位；`SCORE_RANKED_TRIM` 的隐藏换手未评估。 |
| **财务时点硬门槛四** | 尚未开始。 | 基本面因子仍按报告期结束日生效，存在前视偏差；扣非净利润、公告日/首次可得时间、修订版本均未落地。 |
| **运维 V6** | 只读运维工作台、告警候选、失败定位、只读重跑计划已具备。 | 写侧重跑入口仍缺权限、审计、幂等、dry-run/confirm/execute 边界。 |

---

## 2. 核心判断：当前最大矛盾

> **系统已经能跑通「数据 → 因子 → 回测 → 组合 → 模拟盘」的最小闭环，但策略决策仍缺乏「因子有效性证据」和「财务时点正确性」两大金融正确性基础。**

在不具备这两点的情况下继续扩展策略模板或接入更多因子，会放大以下风险：

1. **信号污染**：把无效或共线因子纳入打分，相当于给噪音加权；
2. **未来函数与幸存者偏差**：财务因子按报告期结束日生效，会人为提高历史回测收益；
3. **过度拟合**：在缺乏样本外 IC 证据时调参，最优参数只是全样本拟合结果；
4. **不可解释的执行**：组合约束执行后的权重调整原因虽已记录，但缺少风格/跟踪误差维度，无法评估真实风险暴露。

因此，下一阶段不应继续堆功能，而应把已有闭环**证据化**和**时点正确化**。

---

## 3. 优化目标

将项目从「功能闭环可用」推进到「每个进入策略的因子都有 IC 证据、每个基本面信号都基于公告日可得时间」。

具体目标：

- **3 个月内**：所有活跃因子完成至少一个历史窗口的批量诊断，生成 IC/Rank IC/分层/换手证据；淘汰或降级无证据因子。
- **6 个月内**：财务因子升级到公告日/首次可得时间口径，补扣非净利润因子；历史行业分类可用。
- **9 个月内**：基于 IC 证据做因子族内合成，接入事件/资金类真实数据源，形成可解释的多因子模板族。
- **12 个月内**：风格暴露、跟踪误差、组合波动率约束计算与强制落地，硬门槛三完全关闭。

---

## 4. 分阶段执行计划

### 阶段 A：因子有效性证据化（P0，立即启动）

**范围**：不新增因子口径，只运行和展示已有 82 条因子的截面诊断结果。

**动作**：

1. **运行批量诊断**
   - 使用 `POST /api/v1/factors/batch-profiles` 或 `scripts/factors/run_batch_profiles.py`，对过去 2 年（例如 2024-01-02 至 2025-12-31）的全部活跃因子跑横截面诊断。
   - 推荐参数：`adjust_type=qfq`、`size_factor_name=market_cap`、`size_factor_version=v1`；市值因子不依赖价格复权版本。若市值因子未覆盖全期，可先 `--no-size-neutral` 跑一轮基线。
   - 建议按 category 分批跑，避免单日数据质量导致全批失败；每批写入 `system.job_log`。

2. **建立因子淘汰/分级规则**
   - 初选门槛（可调整，但必须先写出来）：
     - `mean_rank_ic_1d > 0.01` 且 `mean_rank_ic_5d > 0.01`；
     - `trade_date_count >= 200`（覆盖约 1 年交易日）；
     - `mean_coverage_rate >= 0.7`；
     - 分层收益单调性 `quantile_monotonicity_1d` 至少 60% 交易日同向。
   - 把因子分为：
     - **核心池**：多 horizon IC 显著、覆盖率高、换手可控；
     - **观察池**：单 horizon 有效但证据不足；
     - **冻结池**：无 IC 证据或证据为负，暂时禁止进入新策略。

3. **前端/运维展示**
   - 在运维工作台增加「因子 IC 证据看板」：
     - 调用 `GET /api/v1/factors/batch-profiles/summary`；
     - 展示每个因子的 `mean_ic_1d/5d/20d`、`mean_rank_ic_1d/5d/20d`、`trade_date_count`、`mean_coverage_rate`、`has_ic_evidence`。
   - 对冻结池因子生成告警候选。
   - 新增 `POST /api/v1/factors/pool-classifications`：按可配置阈值对全部有 IC 证据的因子做核心池/观察池/冻结池分级；已落地为 `FactorPoolClassifierService` + `scripts/factors/classify_factor_pool.py`。

4. **接入日级调度**
   - 在 `factor_pipeline` 中增加一个可选步骤：`run_factor_batch_profiles`，默认关闭；
   - 当开启时，每日收盘后对最近 lookback_days 窗口增量跑诊断，利用幂等 upsert 只更新不重复。

**验收标准**：

- 至少 50% 活跃因子（约 40 条）进入核心池或观察池；
- 剩余因子有明确的冻结原因（IC 为负、覆盖率低或样本不足）；
- 运维看板可查询全部因子的 IC 证据。

**依赖**：无新增数据源；只需已有 `factor.factor_value` 和 `market.daily_bar` 覆盖所选窗口。

**已交付**（2026-10-14）：

- `FactorPoolClassifierService`：基于 `FactorProfileSummary` 的 IC 证据，按 `min_trade_dates`、`min_coverage_rate`、`min_rank_ic_1d/5d/20d` 把因子分为 `core` / `observation` / `frozen`。
- `FactorPoolPolicy`：默认阈值与 `conservative()` 变体；阈值显式可配置。
- `POST /api/v1/factors/pool-classifications`：接收阈值参数，返回分级结果与原因。
- `scripts/factors/classify_factor_pool.py`：CLI，支持 `--output-format text/json`、`--pool filter`。
- 测试：`test_factor_pool_classifier.py` 覆盖核心/观察/冻结边界、低覆盖、样本不足、保守阈值；`test_api.py` 覆盖 API 转发与 422。
- `FactorProfileSummary` 新增 `category` 字段，从 `factor.factor_definition` 自动关联；缺失时回退为 `unknown`。

---

### 阶段 B：财务时点正确性（P0，与阶段 A 并行）

**范围**：硬门槛四的最小实现；把基本面因子从「报告期结束日生效」改为「公告日/首次可得时间生效」。

**动作**：

1. **数据源升级**
   - 在 `raw.financial` 或新增 `raw.announcement` 中记录：
     - `report_period_end`：报告期结束日；
     - `announcement_date`：公司首次公告日；
     - `available_date`：数据首次可在本系统获得的时间（采集完成日）；
     - `revision_sequence`：原始披露/修正/更正的版本号。
   - 保留原始披露、修正披露、更正公告三个版本；只在修正公告后使用新值。

2. **财务因子计算升级**
   - 修改 `FinancialFactorService` 和 `EarningsRevisionFactorService`，按 `announcement_date` 生效；
   - 在 `factor.factor_run.parameters` 中显式记录 `effective_date_policy=announcement_date_v1`；
   - 对缺失 `announcement_date` 的报告 fail-closed：该报告期不生成因子值。

3. **补扣非净利润因子**
   - 新增 `deducted_earnings_yield`、`deducted_earnings_growth` 等因子；
   - 在 `FactorCategory` 中归属为 `quality` 或 `valuation`，不混用 `momentum`。

4. **金融行业独立口径**
   - 银行、保险、券商使用独立财务指标；在 `market.security` 或 `fundamental.company_profile` 中标记行业子类型；
   - 财务因子计算时按子类型路由到对应公式。

5. **历史行业分类**
   - 新增 `market.industry_history`（symbol, trade_date, industry_code, source）；
   - 行业中性化与行业因子计算读取历史时点行业，不用当前行业回填。

**验收标准**：

- 全部财务因子的 `factor_run.parameters` 包含 `effective_date_policy=announcement_date_v1`；
- 存在至少一个扣非净利润因子并跑通批量诊断；
- 行业中性化使用 `market.industry_history` 而非当前行业；
- 新增测试覆盖「公告日晚于报告期结束日」的未来函数拦截。

**依赖**：

- 公告日数据源（HiThink 财务接口通常提供 announcement_date，可先验证）；
- 历史行业分类数据源（可从聚宽、Tushare 或自建爬虫获取）。

---

### 阶段 C：因子族合成与事件/资金因子接入（P1，在阶段 A 完成后启动）

**范围**：基于 IC 证据做信息源分组，减少共线性；接入事件和资金类真实数据源。

**动作**：

1. **信息源分组**
   - 按 `FactorCategory` 或更细粒度的「信息源标签」（如 `price_momentum`、`volume_pressure`、`earnings_quality`）分组；
   - 对每组内的 `xxx__rank` 或 `xxx__zscore` 做等权或 IC 加权合成，生成 `composite_{group}_score`。

2. **事件类数据源接入**
   - 龙虎榜席位：从东方财富/同花顺获取每日龙虎榜明细，保存到 `raw.lhb_daily`；
   - 涨停结构：从行情数据中识别首板/连板/一字/T字/炸板，保存到 `raw.limit_up_structure`；
   - 股东户数：季度数据源，保存到 `raw.shareholder_count`；
   - 机构调研：保存到 `raw.institutional_research`。

3. **资金类数据源接入**
   - 融资融券：从交易所或 Tushare 获取每日融资余额/买入额/偿还额，替换现有 `margin_financing_change_20` 代理因子；
   - 北向持股：从东方财富获取沪深股通持股；
   - DDX/主力强度：如可获得可靠来源，按 `raw -> market` 流程接入。

4. **新因子族横截面诊断**
   - 对新增事件/资金因子跑批量诊断，确认 IC 证据后再进入策略池。

**验收标准**：

- 至少新增 3 个事件类因子和 3 个资金类因子；
- 每个新增因子都有 `factor_profile` IC 证据；
- 因子族合成产物（`composite_*_score`）有独立的 IC 证据。

---

### 阶段 D：组合风险预算完整化（P1，硬门槛三收尾）

**范围**：在已有行业风险预算基础上，实现风格暴露、跟踪误差、组合波动率约束的计算与强制。

**动作**：

1. **风格因子生产**
   - 基于已有因子定义风格因子：市值（`market_cap`）、价值（`book_to_price`）、动量（`momentum_60`）、波动（`volatility_20`）、流动性（`liquidity_20`）；
   - 对每个风格因子做行业中性化，得到纯风格暴露；
   - 在 `factor_profile` 中记录风格因子 IC 证据。

2. **组合风格暴露约束**
   - `PortfolioRiskBudget.max_style_exposure` 已暴露；在 `PortfolioBuilder` 中计算组合相对基准的风格暴露；
   - 超限按分数或市值偏移调整权重。

3. **跟踪误差与波动率约束**
   - 接入基准收益（沪深 300、中证 500、中证 1000）；
   - 计算组合相对基准的日收益率序列，得到跟踪误差；
   - 计算组合波动率；
   - 超限时通过缩放或截断调整。

4. **风险预算原因记录**
   - 在 `PortfolioTarget.reasons` 中新增 `style_exposure`、`tracking_error`、`volatility` 原因。

**验收标准**：

- `PortfolioBuilder` 对风格暴露、跟踪误差、波动率任一超限都能正确调整权重；
- 调整原因写入 `PortfolioTarget.reasons`；
- 事前风控使用同一风险预算参数。

---

### 阶段 E：滚动 walk-forward 收益复合（P2，可选但重要）

**范围**：解决硬门槛二阶段门中「重叠窗口只标记、不复合收益」的缺口。

**动作**：

1. **窗口切换成本模型**
   - 在滚动序列报告中引入再平衡滑点/佣金；
   - 生成跨窗口复利复合净值曲线。

2. **复合收益报告**
   - 新增 `POST /api/v1/backtests/runs/rolling-composite-report`；
   - 输入一组同一滚动序列的 `backtest_run_id`，输出复合净值、复合收益、复合回撤、复合夏普。

3. **训练产物验证开关**
   - 在研究合同中把 `training_artifact_verified` 从固定 `False` 升级为可配置；
   - 默认仍 `False`，直到有自动校验逻辑。

**验收标准**：

- 滚动序列复合收益曲线与独立窗口收益曲线一致（无未来函数）；
- 复合报告明确标注切换成本假设。

---

## 5. 不建议做的事

1. **不要整表导入知识库 2200+ 字段**：分类错配、时点缺失、高度共线，会污染现有 `FactorCategory` 体系。
2. **不要在没有 IC 证据时新增策略模板**：当前策略模板已够用，缺的是因子质量证据。
3. **不要预设年化收益目标作为验收标准**：硬门槛明确要求权重需通过滚动样本外回测验证，不能用全样本最优反推。
4. **不要把精力投向实盘自动化交易**：本项目不连接券商，真实账户接入不在路线内。V4 训练选参、V5 风控完整化、V6 写侧运维动作均未完成，先把这些做扎实。
5. **不要跳过财务时点治理直接做财务因子族合成**：公告日/首次可得时间是硬门槛四的核心，绕过它会导致所有基本面策略回测不可信。

---

## 6. 推荐下一步动作（本周可启动）

1. **跑一轮全量批量诊断**
   - 选定 2024-01-02 至 2025-12-31，用 `scripts/factors/run_batch_profiles.py` 对所有活跃因子跑诊断；
   - 记录每个因子的成功/失败状态和 IC 摘要。

2. **召开一次 T2 决策评审**
   - 议题：根据诊断结果定义「核心池 / 观察池 / 冻结池」规则；
   - 输出：写入 `aiDoc/research/factor-pool-policy.md`，并禁止冻结池因子进入新策略。

3. **验证财务公告日数据源**
   - 检查 HiThink 财务接口是否返回 `announcement_date`；
   - 用 5-10 只股票的已知财报公告日做 spot check；
   - 输出：公告日字段可用性报告，决定是否启动硬门槛四切片。

---

## 7. 关联文档

- `aiDoc/plans/hard-gates-execution-plan.md`：四项金融正确性硬门槛
- `aiDoc/research/20261013-2021-strategy-optimization-review.md`：知识库差距分析与已实施切片
- `aiDoc/plans/roadmap.md`：项目阶段规划
- `backend/app/services/factor_batch_profiler.py`：批量因子截面诊断服务
- `backend/app/services/factor_pool_classifier.py`：因子池分级服务
- `backend/app/api/routes/factors.py`：因子诊断与分级 API
- `backend/scripts/factors/classify_factor_pool.py`：因子池分级 CLI
