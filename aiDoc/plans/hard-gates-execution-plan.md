# 前置硬门槛执行计划

> 目标：在启动自动选股与策略自进化之前，完成四项金融正确性硬门槛。
> 执行顺序：A股真实交易成本与可交易性 → 滚动 walk-forward → 组合风险预算 → 财务时点正确性。
> 当前阶段：硬门槛一已经完成版本化费用模型、回测可交易性检查与公共 paper 接入执行日行情与可交易性检查；硬门槛二仍为后续规划，当前滚动窗口不能直接执行。

## 1. 总体原则

- 每项硬门槛必须有明确的版本化契约（versioned policy）。
- 回测和模拟盘必须使用同一套成本与可交易性模型。
- 所有变更必须通过回归测试，并更新研究合同。
- 不影响既有成功运行的历史结果；新增参数默认显式声明简化假设。

## 2. 硬门槛一：A股真实交易成本与可交易性

### 2.1 真实交易成本模型 `a_share_cost_v1`

研究 API 与回测服务默认保留 `linear_commission_v1`，只支持双边佣金和滑点。显式选择 `a_share_cost_v1` 可使用以下费用组件；公共 paper 确认和执行使用固定的该模型参数。所有费率都是显式研究假设，当前不按历史日期、交易所或证券类别推导真实费率。

| 费用项 | 当前 | 目标 | 说明 |
|---|---|---|---|
| 佣金 | 双边 `commission_rate` | 双边，含最低佣金 `min_commission` | 买入/卖出均可能触发最低佣金 |
| 印花税 | 无 | 卖出 `stamp_duty_rate = 0.0005` | 2023 年减半后税率 |
| 过户费 | 无 | 双向 `transfer_fee_rate` | 沪市按成交金额比例，深市可忽略或设 0 |
| 交易所规费 | 无 | 双向 `exchange_fee_rate` | 可合并到佣金中或单独列出 |
| 滑点 | `slippage_bps` | 保留 | 买入加价、卖出折价 |

费用模型版本化字段：

- `fee_model_version`: `a_share_cost_v1`
- `fee_model_name`: `a_share_cost`
- `fee_model_components`: `["commission", "min_commission", "stamp_duty", "transfer_fee", "slippage"]`
- `fee_model_limitations`: 明确声明未包含融资融券成本、大宗交易成本等

### 2.2 可交易性规则

A 股特殊交易约束：

1. **涨停不可买入**
   - 买入执行价 ≥ 涨停价时，订单不能成交。
   - 涨停价计算：普通股票为前收盘 × 1.10，ST/*ST 为前收盘 × 1.05。

2. **跌停不可卖出**
   - 卖出执行价 ≤ 跌停价时，订单不能成交。
   - 跌停价计算：普通股票为前收盘 × 0.90，ST/*ST 为前收盘 × 0.95。

3. **停牌日无行情**
   - 停牌证券当日无 `market.daily_bar`。
   - 信号已持仓证券在停牌日仍按最新收盘价估值，但不可交易。

4. **ST/*ST 状态**
   - 需要 `market.security` 或新表记录每日 ST/*ST/退市整理状态。
   - 影响涨跌停幅度。

5. **最小交易单位**
   -  already via `lot_size`，保留。

### 2.3 数据与 Schema 变更

已落地的字段与模型：

- `market.daily_bar` 新增字段：
  - `limit_up`: Decimal | None
  - `limit_down`: Decimal | None
  - `is_suspended`: bool（默认 false）
  - `special_treatment_status`: str | None（标记 ST/*ST 等状态，供后续扩展）
  - `pre_close`: Decimal（已有）
- `raw.daily_bar` 同步新增以上字段， market build 从候选 raw bar 透传。
- `app/backtest/tradability.py` 提供 `AShareTradabilityPolicy`：
  - 停牌日不可买卖；
  - 开盘价 ≥ 涨停价时不可买入；
  - 开盘价 ≤ 跌停价时不可卖出；
  - 缺失 `limit_up`/`limit_down` 时退化为可交易（在费用模型 limitations 中声明）。

### 2.4 代码变更范围

1. `app/backtest/engine.py`
   - `DailyEventBacktestEngine.__init__` 接收 `fee_model` 对象，替代 `commission_rate` / `slippage_bps`。
   - 执行订单前检查涨停/跌停/停牌。
   - 卖出佣金计算加入印花税、过户费。

2. `app/trade/broker.py`
   - `PaperBrokerAdapter` 接收 `FeeModel` 与 `AShareTradabilityPolicy`，`execute` 默认在执行前调用可交易性检查；任一方向被 `limit_up/limit_down/is_suspended/special_treatment_status` 拒绝时抛 `_PaperExecutionBlocked`。
   - 新增 `execute_with_tradability(order, *, enforce_tradability)`，允许确认阶段以 `enforce_tradability=False` 重建订单摘要而不被同一份策略重复阻断；执行路径仍走默认 `execute`，强制执行可交易性。
   - `execute` 返回的 `BrokerFill.commission` 保存全部显式费用合计，当前未分别返回各费用项。

3. `app/services/backtest.py`
   - `DailyBacktestService.run` 支持新的费用参数。
   - 研究合同记录 `fee_model_version` 和全部费用参数。

4. `app/services/paper_execution.py`
   - `EXECUTION_POLICY` 升级到 `a_share_cost_v1` + `v3`，并显式记录 `tradability_policy: enforced_via_execution_day_market_daily_bar`。
   - 模拟盘执行使用与回测一致的费用模型。
   - `PaperExecutionService` 在 `_load_execution_day_bars` 中读取 `market.daily_bar`（adjust_type=`none`）的 `open/limit_up/limit_down/is_suspended/special_treatment_status`，并对每个 risk order 调用 `_assert_execution_day_tradable`（同 `AShareTradabilityPolicy`），任何 `missing_execution_day_bar`、`buy_blocked_by_execution_day_tradability` 或 `sell_blocked_by_execution_day_tradability` 都会以 `status=rejected` 落库并记录 `execution_day_bar_hash`。
   - `execution_policy_hash` 显式包含 `execution_day_bar_hash`，把执行批次绑定到该次预演时使用的执行日行情快照；任何后续修改都会让批次与快照失配，必须重新确认。
   - `BrokerOrderRequest` 在执行 preflight 时附带执行日 bar；`PaperBrokerAdapter.execute` 仍按回测语义强制执行 `AShareTradabilityPolicy`，与研究回测共用同一份可交易性规则。

5. `app/services/paper_approval.py`
   - `PaperExecutionApprovalService.create` 在构造订单批次前调用 `assert_execution_day_tradable`（同 `AShareTradabilityPolicy`），任一方向被禁止或缺失执行日 bar 都会以 `PaperApprovalConflict` 拒绝，不写入确认。
   - `build_paper_order_batch` 接受可选 `execution_day_bars`，并通过新增的 `PaperBrokerAdapter.execute_with_tradability(enforce_tradability=False)` 重新构造订单摘要；确认阶段不再依赖 `broker.execute` 的强校验，避免和预飞重复阻断。真正的执行路径仍走默认 `execute`，强制执行可交易性。
   - 确认阶段的 `risk_evaluation` 哈希、订单批次哈希、确认策略与执行日 bar 一起进入 `approval_hash`；任何执行日行情变化都会让已确认的 approval 与执行批次失配。

6. `app/schemas/strategy.py`
   - `FactorSelectionBacktestRequest` 等 schema 扩展费用字段。

7. 测试
   - `test_backtest_engine.py`: 涨停买入失败、跌停卖出失败、最低佣金、印花税。
   - `test_backtest_service.py`: 新费用模型参数持久化。
   - `test_paper_execution.py`: 模拟盘费用与回测一致；新增 5 个测试覆盖 `test_paper_execution_blocks_buy_at_execution_day_limit_up`、`test_paper_execution_blocks_sell_at_execution_day_limit_down`、`test_paper_execution_blocks_all_when_security_suspended_on_execution_day`、`test_paper_execution_blocks_when_no_execution_day_bar` 和 `test_paper_execution_executes_when_execution_day_is_tradable`。
   - `test_paper_approval.py`: 新增 2 个测试覆盖 `test_create_paper_execution_approval_blocks_buy_at_limit_up` 和 `test_create_paper_execution_approval_blocks_when_no_execution_day_bar`。
   - `test_paper_execution_transactions.py`: 集成路径上的回滚与并发回放测试已注入 `FakeDailyBarRepository` 提供可信未复权执行日 bar，确保测试断言的失败原因不再被新增的 tradability 预飞误吃。

### 2.5 阶段门

- [x] `a_share_cost_v1` 费用模型实现并通过单测（`test_backtest_fees.py`、`test_backtest_engine.py`）
- [x] 涨停/跌停/停牌可交易性检查实现并通过单测（`test_backtest_engine.py`）
- [x] 回测与模拟盘共用版本化费用组件；研究默认保留线性模型，paper 策略固定使用 `a_share_cost_v1`
- [x] 研究合同正确记录 `fee_model_version` 和全部费用参数
- [x] 公共 paper 接入执行日行情与可交易性检查：确认阶段由 `app/services/paper_approval.py::assert_execution_day_tradable` 预飞；执行阶段由 `app/services/paper_execution.py::_assert_execution_day_tradable` 强制，并以 `execution_day_bar_hash` 绑定执行批次快照
- [ ] 核验证券类别、历史费率和缺失涨跌停数据边界，完成完整金融正确性验收；组件测试通过不等于阶段门通过

## 3. 硬门槛二：滚动 walk-forward 验证

### 3.1 目标

把当前仅规划的滚动窗口升级为可执行、可持久化、可生成验证报告的流程。

### 3.2 关键设计

1. **训练产物不可变存储**
   - 每个训练窗口生成 `strategy.training_artifact`。
   - 当前保存训练期因子数据快照（`training_input_snapshot.factor_frame`），并写入 `artifact_hash`（SHA-256）。
   - 未来引入机器学习模型或因子权重优化时，产物可扩展为包含训练出的权重/参数/规则文件。
   - 按 `window_id` 幂等创建：同一窗口重复执行时复用已有 artifact。

2. **窗口到运行绑定**
   - 每个滚动窗口生成一个独立的 `strategy.backtest_run`。
   - `backtest_run.training_artifact_id` 外键绑定到本窗口的训练产物。
   - 研究合同记录 `validation_mode="rolling"`、`validation_protocol="rolling_walk_forward_v1"`、`validation_window_id` 和 `window_binding_status="artifact_bound"`。

3. **执行接口**
   - 新增 `POST /strategy-research/rolling-backtests`：给定滚动窗口规划与公共策略参数，逐个窗口创建 artifact 并执行回测。
   - `/strategy-research/factor-selection/backtests` 与 `/factor-selection/backtests/batch` 继续拒绝 `validation_mode="rolling"`，因为单个请求无法表达滚动窗口参数。

4. **验证报告增强**
   - `ResearchValidationReportService.build_report` 已支持按 `validation_window_id` 前缀聚合同一滚动序列的 runs；本次把 `validation_window_id` 固定为 `rolling:{exchange}:...` 格式，报告可直接复用现有聚合逻辑。
   - 复合收益曲线（考虑窗口切换成本）尚未实现，在阶段门中明确标记为 P1 后续项。

### 3.3 阶段门

- [x] 滚动窗口可执行并生成独立 backtest_run：`app/services/strategy_research.py::run_rolling_factor_selection` 已落地，`POST /strategy-research/rolling-backtests` 已开放
- [x] 训练产物进入不可变存储：`strategy.training_artifact` 表新增 `artifact_hash`，按 `window_id` 幂等创建，训练期因子快照完整落库
- [x] 验证报告自动聚合滚动序列：研究合同写入 `validation_window_id`，现有报告服务可直接按窗口 ID 聚合
- [ ] 重叠窗口只标记、不复合收益的问题解决或明确声明：当前每个窗口独立报告收益，未跨窗口复利复合；需要在验证报告中显式说明切换成本与未复合边界，后续作为 P1 增强

## 4. 硬门槛三：组合风险预算与风格暴露

### 4.1 目标

在组合构建阶段引入行业集中度、风格暴露和基准跟踪误差约束。

### 4.2 关键设计

1. **风险预算对象**
   - `PortfolioRiskBudget`：最大行业权重、最大风格暴露、最大跟踪误差、最大组合波动率。

2. **风格暴露计算**
   - 接入 Barra 风格因子或自研风格因子（市值、价值、动量、质量、波动、流动性）。
   - 计算组合相对基准的主动暴露。

3. **组合构建升级**
   - `PortfolioBuilder.build` 接收 `risk_budget`。
   - 在权重约束后加入风险预算约束。

4. **指数增强支持**
   - 允许指定 benchmark。
   - 约束跟踪误差 ≤ 目标值。

### 4.3 阶段门

- [ ] 组合构建支持行业、风格、跟踪误差约束
- [ ] 风险预算违反时有明确原因记录
- [ ] 事前风控使用同一套风险预算

## 5. 硬门槛四：财务/基本面因子时点正确性

### 5.1 目标

所有基本面因子使用公告日或首次可得时间，而不是报告期结束日。

### 5.2 关键设计

1. **财务数据升级**
   - `market.financial` 或新表记录 `announcement_date`、`report_period_end`。
   - 因子计算时按 `announcement_date` 生效。

2. **报告版本**
   - 保留原始披露、修正披露、更正公告的版本。
   - 只在修正公告后使用新值。

3. **金融行业独立口径**
   - 银行、保险、券商使用独立财务指标。

4. **历史行业分类**
   - 使用历史时点行业分类，不用当前分类回填。

### 5.3 阶段门

- [ ] 所有财务因子能追溯到公告日
- [ ] 报告修订版本化
- [ ] 金融行业使用独立口径
- [ ] 历史行业分类可用

## 6. 执行顺序与依赖

```
阶段 1: A股真实交易成本与可交易性
   │ 依赖：market.daily_bar 有 pre_close，新增 limit_up/limit_down 和 security_status
   │ 时间：4-6 周
   ▼
阶段 2: 滚动 walk-forward 验证
   │ 依赖：阶段 1 完成、训练产物存储
   │ 时间：4-6 周
   ▼
阶段 3: 组合风险预算与风格暴露
   │ 依赖：阶段 2 完成、风格因子可用
   │ 时间：6-8 周
   ▼
阶段 4: 财务/基本面因子时点正确性
   │ 依赖：公告日数据源、历史行业分类
   │ 时间：8-12 周
```

## 7. 结论

四项硬门槛完成后，系统才具备支持自动选股和策略自进化的金融正确性基础。在此之前，任何自动选股结果都只能作为研究参考，不能进入模拟盘或实盘。

## 8. 硬门槛一执行日可交易性接入评审记录

> 触发原因：把 `AShareTradabilityPolicy` 从回测引擎提升到公共 paper 确认与执行两阶段，并把执行日行情快照写入执行批次哈希。这是公共 API 行为、批次元数据 schema 与项目阶段定位的同步变化，按 `aiDoc/plans/decision-review-protocol.md` 第 1 节判定为 T2。
> 受影响模块与文档：`backend/app/trade/broker.py`、`backend/app/services/paper_approval.py`、`backend/app/services/paper_execution.py`、相关测试、`aiDoc/plans/roadmap.md`、`aiDoc/relations/system-map.md`、`README.md`、本文件。
> 当前阶段：V6.1 只读运维工作台收口后，转向 V5 公共操作闭环；硬门槛一为公共 paper 金融正确性的先决条件。

### 四角色结论

- 架构师：通过。`PaperExecutionService` 与 `PaperExecutionApprovalService` 只在已有 preflight 链路中插入 `_load_execution_day_bars` + `_assert_execution_day_tradable`，与 `AShareTradabilityPolicy` 在回测引擎的接入方式一致；`PaperBrokerAdapter.execute_with_tradability` 显式拆出"是否强制校验"以避免确认阶段被同一份策略重复阻断；执行批次哈希通过 `execution_day_bar_hash` 把批次绑定到执行日行情快照，避免"批次已签发但行情已变"的脏读。事实源没有变化，仍为 `market.daily_bar`。
- 产品经理：通过。`/paper-execution-approvals/.../executions` 与 `/risk-evaluations/.../confirmations` 的公共契约保持稳定，失败原因以 `failure_code` 暴露 `missing_execution_day_bar` / `buy_blocked_by_execution_day_tradability` / `sell_blocked_by_execution_day_tradability`，Vue3 工作台可在不破坏现有确认/执行交互的前提下把阻断原因展示给操作者；MVP 边界未变，仍是 paper-only、local single-user、确定性 full-fill。
- 金融行业专家：有条件通过。`AShareTradabilityPolicy` 与回测引擎共用同一份规则，涨停买入、跌停卖出、停牌、ST 振幅等基础规则已覆盖；当前未在 `market.daily_bar` 上对全量证券每日落地 ST/*ST/退市整理历史时点，缺失 `limit_up/limit_down` 时策略退化为可交易（已在 `tradability.py` 注释中显式声明），属于"显式研究假设"而非隐藏默认。这条限制继续以 P1 写在阶段门 2.5 最后一项。
- Vibe Coding 规则编写者：通过。决策评审协议要求本次变更必须出 T2 报告并落到 `aiDoc/`；`hard-gates-execution-plan.md` 阶段门由"部分完成"改为"已通过公共 paper 接入"，`roadmap.md` / `system-map.md` / `README.md` 已同步，触发条件可判定（"硬门槛一执行日可交易性接入"），引用式读取不破坏现有规则层级；测试门槛已由 23 条 paper 相关测试覆盖（其中 7 条新增）。

### 金融硬检查（节选）

- 时点正确性：执行日 bar 仅用于可交易性判断与快照哈希，不参与成交价与现金计算；成交价沿用 `risk_check` 绑定的 reference price。
- 复权与公司行为：执行日读取 `adjust_type=none` 可信未复权 bar，与已有成交价口径一致；公司行为落地前命中除权除息区间的回测仍 fail-closed。
- 偏差与资金风险：缺 bar 或可交易性拒绝的订单不再产生任何成交、订单、现金或持仓变更；执行批次落库为 `status=rejected`，`approval.status=consumed`，不能重新消费。
- 复现与审计：执行批次 `execution_policy_hash` 显式包含 `execution_day_bar_hash`，可与 `market.daily_bar` 任意时刻快照对账。
- 人工确认边界：本次未触碰确认 TTL、人工确认短语或 V7 实盘写侧边界。

### P0 / P1 阻断项

- P0：无新增阻断项。
- P1（关闭）：执行日 bar 读取、tradability 预飞、批次快照绑定、failure_code 暴露、回滚与并发回放测试已全部交付（`test_paper_execution.py::test_paper_execution_blocks_*` 4 条 + `test_paper_execution_executes_when_execution_day_is_tradable` 1 条 + `test_paper_approval.py::test_create_paper_execution_approval_blocks_*` 2 条 + `test_paper_execution_transactions.py` 注入 `FakeDailyBarRepository`）。
- P1（未关闭，仍在阶段门 2.5）：证券类别 / 历史费率 / 缺失涨跌停数据边界与全量时点历史数据；与本次 T2 决策不直接耦合，将在硬门槛一剩余阶段门单独评审。

### P2 优化建议

1. （本次必做）`AShareTradabilityPolicy` 在缺失 `limit_up/limit_down` 时退化"可交易"的当前默认值，在 `aiDoc/research/decision-factor-framework.md` 或 `aiDoc/data/daily-bar-contract.md` 中显式记录覆盖与回退条件，避免后续维护者误以为是隐式策略。本次 PR 范围内未触及该文档，可在阶段门 2.5 收口前一并补齐。
2. （后续候选）`daily_bar_repository.list_by_symbols_on_date` 当前在 `paper_execution` 与 `paper_approval` 两处直接调用，未来可抽出 `market_daily_bar_service` 共享；如果继续出现第三个调用方再统一。
3. （后续候选）`execution_day_bar_hash` 只在 rejected/completed 批次中保存快照摘要，不持久化完整 bar 行；如需做执行日 bar 事后审计，可补一张 `trade.execution_day_bar_snapshot` 表并与本批次外键关联。

### 测试与验证

- 全部后端测试：590 passed（含 23 条 paper 相关，其中 7 条新增）。
- Ruff：`All checks passed!`。
- 关键场景单元测试：涨停买/跌停卖/停牌/缺 bar/可交易，五类阻断路径与一条成功路径均显式断言 `failure_code` 与 `rejected/completed` 状态。

### 文档同步

- `aiDoc/plans/hard-gates-execution-plan.md` 第 5 节顶部状态、2.4 节代码变更范围（paper_execution、paper_approval、broker）、2.5 节阶段门（公共 paper 接入项改为已完成）；
- `aiDoc/plans/roadmap.md` V4.1 与 V5.3 章节加入可交易性强制与 `execution_day_bar_hash`；
- `aiDoc/relations/system-map.md` 事实源边界与"文档同步触发"小节；
- `README.md` 当前状态与"模拟执行确认" / "模拟执行"两条 API 描述；
- 本节为 T2 决策评审报告，留在 `hard-gates-execution-plan.md` 作为稳定结论。

### 明确不做

- 不引入券商可交易性或交易所级别的可交易性规则；当前以 `AShareTradabilityPolicy` 单一策略为准。
- 不修改 `a_share_cost_v1` 费用模型参数；本次仅在可交易性层面提升。
- 不开放历史 paper 重放（replay only handles current approval 哈希，不重放已执行 / 已拒绝批次）。
- 不修改 V5.1 / V5.2 已交付的 readiness、ADV、风险政策哈希链路。

### 最终结论

允许提交。后续阶段门 2.5 剩余 P1（证券类别、历史费率、缺失涨跌停数据边界）继续按独立切片推进，不在本次提交范围。

## 9. 硬门槛二滚动 walk-forward 可执行化评审记录

> 触发原因：新增 `POST /api/v1/strategy-research/rolling-backtests`，在数据库引入 `strategy.training_artifact.artifact_hash`、通过 `backtest_run.training_artifact_id` 绑定窗口、并在研究合同中写入 `validation_protocol="rolling_walk_forward_v1"` 和 `window_binding_status="artifact_bound"`。该变更新增公共 API、schema、表字段和主链路上的金融语义，按 `aiDoc/plans/decision-review-protocol.md` 第 1 节判定为 T2。
> 受影响模块与文档：`backend/app/services/strategy_research.py`、`backend/app/services/rolling_backtest.py`、`backend/app/models/strategy/backtest.py`、`backend/app/database/session.py`、`backend/app/repositories/strategy/training_artifact.py`、`backend/app/repositories/strategy/backtest.py`、`backend/app/schemas/strategy.py`、`backend/app/api/routes/strategy_research.py`、相关测试、`aiDoc/plans/roadmap.md`、`README.md`、本文件。
> 当前阶段：V6.1 只读运维工作台收口后，V5 公共操作闭环正在收尾；硬门槛一已提交，硬门槛二是自动选股/策略自进化的前置金融正确性条件。
> 评审方式：当前运行环境子代理评审被中断，采用主代理分角色独立评审（fallback: 主代理分角色评审）。

### 四角色结论

- 架构师：通过。新增 `StrategyResearchService.run_rolling_factor_selection` 与 `RollingFactorSelectionBacktestRequest` 保持既有分层：`api/routes/strategy_research.py` 只做 HTTP 参数转换；service 负责窗口生成、因子读取、artifact 创建和回测编排；repository 仅负责持久化；`BacktestRun.training_artifact_id` 是 `strategy.backtest_run` 到 `strategy.training_artifact` 的单向外键，依赖方向正确。`database/session.py` 的 `apply_schema_compatibility` 已为旧库追加 `artifact_hash` 列和索引，迁移路径幂等。`run_rolling_factor_selection` 对同一 `window_id` 调用 `get_by_window_id` 命中即复用，避免重复 artifact。
- 产品经理：通过。新增端点明确拒绝在 `/factor-selection/backtests` 和 `/factor-selection/backtests/batch` 中使用 `validation_mode="rolling"`，把滚动参数隔离到专用端点，语义清晰。`RollingWindowRead.execution_status` 扩展为 `planning_only | artifact_created | run_completed`，未来可支持分阶段 UI。MVP 边界未变：滚动执行仍由调用方给定公共策略参数，平台内自动训练选参尚未实现，符合 roadmap 所述。
- 金融行业专家：有条件通过。训练期因子快照通过 `StrategyFactorFrame.from_rows(as_of_date=training_end_date, ...)` 聚合，框架会拒绝 `trade_date > as_of_date` 的观测，未来函数在框架层面被拦截；测试期回测严格只使用测试期数据，训练数据不混入测试期，符合 walk-forward 基本要求。但是：
  1. 幂等复用逻辑按 `window_id` 命中即复用，若两次执行间因子数据被回填/修正，旧 artifact 会被静默复用，导致后续回测结果与当前 `market.factor_value` 不一致；
  2. 研究合同中 `training_artifact_verified` 固定为 `False`， artifact 内容是否真正对应训练期因子未经验证；
  3. 相邻窗口测试期可能重叠，验证报告只标记、不复合收益，调用方若误用独立收益做复合会高估表现。上述三点已分别作为 P1 阻断项记录。
- Vibe Coding 规则编写者：通过。触发条件可判定（新增公共 API 和 schema），文档同步已覆盖 `aiDoc/plans/hard-gates-execution-plan.md`、`aiDoc/plans/roadmap.md`、`README.md`；引用式读取未破坏规则层级；测试新增 2 个滚动执行/幂等复用测试，并对 `rolling` 拒绝文案做了断言。`AGENT.MD` 第 162 条要求新文档文件以时间戳前缀命名，本次未新增文档文件，仅修改既有文件，符合要求。

### 金融硬检查

- 数据血缘：训练期因子来自 `factor.factor_value`（经 `factor_value_repository.list_for_cross_section`），证券类型来自 `market.security`，符合 `raw -> market -> factor -> research` 链路。
- 未来函数：`StrategyFactorFrame.__post_init__`（`backend/app/strategies/inputs.py:58`）会拒绝 `trade_date > as_of_date` 的观测，训练期 `as_of_date` 取 `training_end_date`，测试期 `as_of_date` 取测试区间内的每个信号日，训练期数据不会前视到测试期。
- 样本泄漏：`run_rolling_factor_selection` 在每个窗口内先读取训练期因子、创建 artifact，再用测试期日期运行回测；训练数据不传入 `_run_single_factor_selection` 的测试期参数。
- 复现与审计：每个窗口的 `training_artifact` 保存 `artifact_hash`（SHA-256）和完整 `training_input_snapshot`；`backtest_run.training_artifact_id` 外键绑定，研究合同写入 `validation_window_id` 和 `window_binding_status`。
- 资金风险：本次只涉及研究层，不触碰 paper/实盘执行链路，无真实资金风险。

### P0 / P1 阻断项

- P0：无。
- P1（本次已关闭 / 已记录）：
  1. `training_artifact` 按 `window_id` 幂等复用但不对 `artifact_hash` 做一致性校验：已修复。`StrategyResearchService._get_or_create_training_artifact` 在命中已有 artifact 时比较当前计算 hash 与库中 `artifact_hash`，不一致抛 `ValueError("content hash does not match ...")`（`backend/app/services/strategy_research.py:205`），避免静默复用回填数据。
  2. `training_artifact_verified` 固定为 `False`：研究合同明确声明产物未验证，当前不宣传为"已验证 walk-forward"。
  3. 重叠窗口与未复合收益：文档已在阶段门 3.3 最后一项标记为未解决，后续切片处理。
- P2 优化建议：
  1. （后续候选）把滚动执行的结果聚合与复合收益作为独立切片，引入窗口切换成本（如再平衡滑点/佣金）并生成复合净值曲线。
  2. （后续候选）`training_artifact` 内容未来可扩展为包含训练出的模型权重/参数/规则文件，当前因子快照结构已为该扩展预留空间。

### 测试与验证

- 全部后端测试：592 passed，20 warnings，0 failed。
- Ruff：`All checks passed!`。
- 新增/调整测试：`test_strategy_research_service.py` 新增 `test_strategy_research_service_runs_rolling_factor_selection` 和 `test_strategy_research_service_reuses_existing_training_artifact`，覆盖 4 窗口执行、artifact 创建、合同字段和幂等复用；`test_api.py` 更新对 `rolling` 拒绝文案的断言；`test_database_initialization.py` 同步 `TrainingArtifact` 构造。

### 文档同步

- `aiDoc/plans/hard-gates-execution-plan.md` 第 3 节状态与阶段门更新，新增本节评审记录。
- `aiDoc/plans/roadmap.md` V4.1/V4.3 同步滚动窗口可执行、artifact 绑定、未验证与未复合边界。
- `README.md` 核心 API 面与研究状态已包含 `rolling-backtests` 说明。

### 明确不做

- 不实现平台内自动训练选参与参数敏感性分析。
- 不生成跨窗口复利复合收益曲线。
- 不引入训练产物内容验证开关（当前固定未验证）。
- 不修改 V4.1 回测引擎、费用模型和可交易性策略。

### 最终结论

允许提交。P1 项中第 1 项（artifact hash 校验）建议提交前快速补齐；若本次不补齐，则在最终结论中明确记录为附条件提交，责任在后续切片关闭。
