# 前置硬门槛执行计划

> 目标：在启动自动选股与策略自进化之前，完成四项金融正确性硬门槛。
> 执行顺序：A股真实交易成本与可交易性 → 滚动 walk-forward → 组合风险预算 → 财务时点正确性。
> 当前阶段：硬门槛一部分落地：版本化费用模型与回测可交易性检查已有实现，公共 paper 尚未接入执行日行情检查，完整阶段门未通过。硬门槛二仍为后续规划，当前滚动窗口不能直接执行。

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
   - `PaperBrokerAdapter` 接收 `FeeModel`。
   - `execute` 返回的 `BrokerFill.commission` 保存全部显式费用合计，当前未分别返回各费用项。

3. `app/services/backtest.py`
   - `DailyBacktestService.run` 支持新的费用参数。
   - 研究合同记录 `fee_model_version` 和全部费用参数。

4. `app/services/paper_execution.py`
   - `EXECUTION_POLICY` 升级到 `a_share_cost_v1`。
   - 模拟盘执行使用与回测一致的费用模型。
   - 当前公共模拟盘按已绑定参考价进行 full-fill 模拟，不传入执行日行情，尚不执行涨跌停/停牌检查；执行策略显式记录此限制，不能把回测中的可交易性检查等同于模拟盘已接入。

5. `app/schemas/strategy.py`
   - `FactorSelectionBacktestRequest` 等 schema 扩展费用字段。

6. 测试
   - `test_backtest_engine.py`: 涨停买入失败、跌停卖出失败、最低佣金、印花税。
   - `test_backtest_service.py`: 新费用模型参数持久化。
   - `test_paper_execution.py`: 模拟盘费用与回测一致。

### 2.5 阶段门（部分完成）

- [x] `a_share_cost_v1` 费用模型实现并通过单测（`test_backtest_fees.py`、`test_backtest_engine.py`）
- [x] 涨停/跌停/停牌可交易性检查实现并通过单测（`test_backtest_engine.py`）
- [x] 回测与模拟盘共用版本化费用组件；研究默认保留线性模型，paper 策略固定使用 `a_share_cost_v1`
- [x] 研究合同正确记录 `fee_model_version` 和全部费用参数
- [ ] 公共 paper 接入执行日行情与可交易性检查，并与回测保持一致的明确边界
- [ ] 核验证券类别、历史费率和缺失涨跌停数据边界，完成完整金融正确性验收；组件测试通过不等于阶段门通过

## 3. 硬门槛二：滚动 walk-forward 验证

### 3.1 目标

把当前仅规划的滚动窗口升级为可执行、可持久化、可生成验证报告的流程。

### 3.2 关键设计

1. **训练产物不可变存储**
   - 每个训练窗口生成 `training_artifact`（因子权重、参数、筛选规则）。
   - 写入 lake release 或专用表，记录 SHA-256。

2. **窗口到运行绑定**
   - 每个滚动窗口生成一个独立的 `backtest_run`。
   - `backtest_run.parameters` 记录 `rolling_window_id`、`training_artifact_id`。

3. **执行接口**
   - 新增 `POST /strategy-research/rolling-backtests`：给定滚动窗口规划，逐个执行。
   - 或扩展 batch 接口允许 `validation_mode="rolling"`。

4. **验证报告增强**
   - 自动聚合同一滚动序列的 runs。
   - 提供复合收益曲线（考虑窗口切换成本）。

### 3.3 阶段门

- [ ] 滚动窗口可执行并生成独立 backtest_run
- [ ] 训练产物进入不可变存储
- [ ] 验证报告自动聚合滚动序列
- [ ] 重叠窗口只标记、不复合收益的问题解决或明确声明

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
