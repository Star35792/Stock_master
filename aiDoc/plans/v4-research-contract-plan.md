# V4 单策略研究合同与可复现回测计划

> 本文保留最初单次研究合同切片及带日期的后续记录。当前已交付基准/比较/批量与专用滚动执行，详见 [路线图](roadmap.md)；下文早期“后续任务”及 2026-09-18 的 `planning_only` 描述不代表当前执行能力。

## 1. 目标与边界

本计划落实 V4 当前切片：完成“单策略、单参数集、单次回测”的研究合同闭环，将现有“能跑、能查”的回测内核提升为可追溯、可复现、可回放的研究能力。

本切片必须满足：

- 策略、因子、数据、复权、调仓、持有期、费用、滑点、最小交易单位和代码版本均有明确记录。
- 回测结果可以追溯到完整研究输入及其版本。
- 相同输入重复运行时，交易、净值、持仓和绩效结果一致。
- 回测只消费可信 `market` 和 `factor` 层，不直接读取 `raw` 或外部接口。
- 通过现有研究 API 创建、运行、查询和分析一次回测。

明确不做：

- 批量参数运行、跨运行比较。
- 样本外验证、滚动窗口验证和参数敏感性分析。
- 基准收益、超额收益和基准比较 API；基准定义只作为合同元数据预留，基准比较属于后续 V4 切片。
- 新增因子、V5 公共交易 API、真实账户接入和自动化交易（永久不在项目范围）。

## 2. 研究合同

在现有因子选股请求上固化标准合同，至少包含：

- `strategy_name`
- `factor_specs`：因子名称、因子版本、权重和方向
- `signal_lag`
- `rebalance_rule`
- `holding_period`
- `benchmark`（仅记录定义，不计算比较结果）
- `adjust_type`
- `allowed_security_types`
- `start_date`、`end_date`
- `initial_cash`
- `commission_rate`、`slippage_bps`、`lot_size`
- `code_version`、`data_version`

合同进入 Service 层后必须规范化：

- 因子规格按固定顺序排列。
- 证券代码统一大写并排序。
- Decimal 参数统一使用字符串表示。
- JSON 使用稳定字段顺序序列化。
- 生成的信号与原始因子规格同时保存，不能只保存信号结果而丢失因子版本信息。

## 3. 实施顺序

### 3.1 固化 Schema 和 API 输入

修改现有研究请求和回测运行响应，使研究合同成为稳定边界。API 只负责 HTTP 语义、参数校验和响应转换，不负责回测编排。

涉及文件：

- `backend/app/schemas/strategy.py`
- `backend/app/api/routes/strategy_research.py`
- `backend/app/api/routes/backtests.py`

### 3.2 统一研究 Service 和回测 Service

`StrategyResearchService` 负责合同校验、因子版本读取、证券类型过滤和信号生成；`DailyBacktestService` 负责可信行情读取、确定性排序、次日执行、结果持久化和失败状态记录。

必须保持：

- 信号至少滞后一个交易日执行，禁止当日收盘信号当日成交。
- 因子和行情只读取可信数据。
- 信号日期必须位于回测区间内。
- 调整方式、费用、滑点和最小交易单位进入运行参数。
- 运行记录保存完整研究合同、代码版本和数据版本。

涉及文件：

- `backend/app/services/strategy_research.py`
- `backend/app/services/backtest.py`
- `backend/app/backtest/engine.py`
- `backend/app/repositories/strategy/backtest.py`

优先复用现有 `strategy.backtest_run.parameters` JSONB 保存合同，不新增表；只有当查询或约束需求无法由现有结构满足时，才另行提出 schema 变更。

### 3.3 完善运行详情和查询

回测运行详情应能直接还原研究合同，现有运行、交易、净值、持仓和绩效查询路由继续复用，不新增跨运行比较接口。

涉及文件：

- `backend/app/schemas/strategy.py`
- `backend/app/services/backtest.py`
- `backend/app/api/routes/backtests.py`

### 3.4 建立确定性回归测试

至少覆盖：

- 合同字段和边界校验。
- 因子名称/版本缺失时拒绝运行。
- 信号日期越界时拒绝运行。
- 非 trusted 行情不会进入回测。
- 信号只在下一交易日执行。
- 相同输入重复运行时交易、净值、持仓和绩效一致。
- 运行参数包含因子版本、数据版本、代码版本和完整交易参数。
- 失败运行保留 `failed` 状态和错误信息。
- 查询接口可以还原完整合同。

涉及文件：

- `backend/tests/test_strategy_research_service.py`
- `backend/tests/test_backtest_service.py`
- `backend/tests/test_backtest_engine.py`
- `backend/tests/test_backtest_query_service.py`
- 必要时补充研究路由测试

## 4. 金融与数据硬检查

本切片完成前逐项确认：

- `market -> factor -> research/backtest` 血缘可追溯。
- 收盘后信号不会在当日成交。
- 复权方式显式记录，成交量和成交金额不被错误复权。
- 费用、滑点、容量相关的最小交易单位显式记录。
- 缺失数据不静默填零，非 trusted 行情不得进入回测。
- 财务因子公告日/首次可得时间尚未完全升级的问题必须保留为风险说明，不得声称本计划已经解决。

## 5. 四角色评审结论

### 架构师：有条件通过

复用现有分层和 `parameters JSONB`，合同规范化放在 Service 层，避免 API 直接组织持久化；通过确定性排序和独立测试保证可测试性与演进成本可控。

### 产品经理：通过

范围直接对应当前最高优先级，只解决可复现和追溯问题，不提前建设批量研究、比较平台或 V5 工作流。

### 金融行业专家：有条件通过

必须执行次日成交、版本追溯、复权和交易成本记录；财务因子的公告时点风险继续显式标记，未验证项不得默认为通过。

### Vibe Coding 规则编写者：通过

优先修改现有 Schema、Service、API 和测试；不创建无必要抽象；实现完成后执行测试、lint 和文档影响检查。

## 6. 阶段门与验证

阶段门：

1. 研究合同能够通过 API 创建并持久化。
2. 运行详情能够还原完整输入版本。
3. 回测结果来自可信数据层并遵守次日执行规则。
4. 相同输入重复运行结果一致。
5. 运行、查询、归因和失败状态均有回归测试。
6. 后端 `pytest`、Ruff 检查通过；若 API 影响前端，再执行前端构建。
7. 完成 `aiDoc/plans/roadmap.md` 和必要 API 文档同步。

## 7. 文档与后续 V4

本计划是 V4 当前切片的正式执行依据。实现完成后更新路线图状态；若请求/响应契约变化，同步 README 核心 API 面。

后续 V4 任务另立计划，依次考虑：

1. 基准收益和超额收益计算。
2. 样本外与滚动窗口验证。
3. 回测结果跨运行比较。
4. 批量参数运行和结果漂移检查。

这些能力不因本切片测试通过而自动纳入当前实现。

历史切片状态（2026-09-18，已被后续滚动执行实现覆盖）：最小 `out_of_sample` 守卫已落地，service 层强制训练期早于测试期，因子读取和回测都只覆盖测试期，研究合同记录显式测试边界、`holdout_v1` 和确定性窗口标识；由于训练/选参产物尚无不可变存储与窗口绑定，合同明确标记训练产物未验证、绑定仅为记录。滚动窗口接口当前只提供 `planning_only` 规划结果，`rolling` 单次与批量执行均 fail-closed，不得表述为已实现 walk-forward。

同一后续切片新增只读验证报告：调用方显式提供既有 run IDs，服务端校验研究合同、代码版本和数据版本可比性，逐窗保留失败或缺绩效事实，并只对成功窗口聚合统计。测试窗口可能重叠，因此报告只标记重叠而不提供跨窗复合收益。该报告不创建实验、不选参、不写训练产物，也不改变任何运行状态。

后续研究正确性加固（2026-09-18）：横截面策略和指数择时模板统一消费不可变 `StrategyFactorFrame`。全区间因子行只用于编排，策略决策入口每次只能获得 `as_of_date == signal_date` 的单日 frame；future row、重复 `(factor, symbol, trade_date)` 和跨日 decision frame 均 fail-closed。研究合同新增 `strategy_input_snapshot`，记录 `strategy_factor_frame_v1`、区间截至日、行数、`factor_run_ids` 和规范化内容 SHA-256。该能力只能证明已进入可信因子层后的 observation-date 边界，不能替代公告首次可得时间、财务修订版本、历史成分和幸存者偏差治理。

同一加固把事件引擎版本化为 `daily_event_v2`：服务按证券交易所读取可信交易日历，当前明确识别 `SH/SZ/BJ/HK` 后缀，未知交易所、缺失或空交易日历均 fail-closed；D 日信号固定安排到下一开市日，执行日缺可信未复权 bar 时拒绝运行，不再等待该证券之后恢复行情。同侧订单及输出使用稳定证券代码顺序，重复 bar/信号直接拒绝。运行参数保存 `trusted_market_slice_v1` 内容摘要，覆盖实际消费的未复权行情、公司行为和执行日历；计算完成后使用独立 SQLAlchemy session 重读并比较，摘要变化以 `backtest_data_changed` 失败，不写成功结果。该摘要补充而不替代 lake release/checksum；未绑定不可变 release 仍是后续血缘工作。

研究 API 与回测服务默认保留显式简化的 `linear_commission_v1`，支持双边线性佣金和滑点；调用方可以显式选择 `a_share_cost_v1`，增加最低佣金、卖出印花税、过户费和交易所规费，并将全部费用假设写入合同。费率是研究假设，不代表逐历史交易日或逐证券的真实适用费率；规费与佣金不得重复计入。合同仍用 `unquantized_decimal_until_persistence_v1` 标记金额精度政策。公共 paper 确认与执行采用固定 `a_share_cost_v1` 参数及 2 bps 滑点，按执行日可信未复权行情检查涨跌停/停牌等约束，仍是确定性 full-fill 模拟，不模拟队列、冲击成本或部分成交。金样本只冻结当前已经裁决的研究语义，不放行自动化交易，也不把旧 `Trade_agent` 的双库、CLI、Web 或单 lot 限制引入当前系统。
