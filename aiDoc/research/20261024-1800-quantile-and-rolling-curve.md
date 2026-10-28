# 分层回测与多窗口组合净收益曲线

日期：2026-10-24
关联：`aiDoc/plans/roadmap.md`、`backend/app/backtest/quantile.py`、`backend/app/backtest/curve_stitch.py`、`backend/app/backtest/quantile_result.py`、`backend/app/services/strategy_research.py`

## 背景与目标

因子选股回测引擎此前只能产出「主策略一份净值」。要稳定评估因子有效性与策略净收益，需要两类可比视图：

- **A. 分层回测（quantile 组合）**：单次回测内按某因子横截面排序分 `n` 层，每层取该桶标的多头等权各自真实回测（含费用），产出每层净值曲线 + top–bottom 统计收益差。用来判断因子单调性（收益是否随因子值单调变化），而不是只盯 top_n 一档。
- **B. 多窗口净收益曲线拼接**：滚动回测（rolling）中把各窗口独立 equity_curve 按 test 区间首尾衔接、归一化本金并汇总换手/佣金，产出一条组合净收益曲线。用来把 walk-forward 各窗结果合成一条可比的累计曲线。

这两项都**不新建 DB 表**、**不重算成本**（成本已在引擎逐日计入），结果内嵌主运行 `parameters` 的 JSON 供审计。

## 四角色评审裁决

评审角色：架构师 / 产品经理 / 金融行业专家 / Vibe Coding 规则编写者。采纳项：

- **重叠窗口拒绝（P0）**：拼接遇到重叠 test 窗直接 fail-closed，避免重叠段收益被重复计、虚增组合曲线。
- **run 级 wrapper**：`combined` 曲线挂在滚动运行级 `RollingBacktestResult`，而非塞进 per-window dataclass；并新增对应 API 响应 schema `RollingBacktestResultRead`。
- **未来函数守卫（P0）**：分层分桶只在决策日（signal_date）取 split 因子的**当日可得值**，不向前看；决策日集合与主策略一致（daily 取全部观测日，非 daily 取再平衡日交集）。
- **默认 `None` 不写 parameters**：`quantile_backtest` 缺省时不向 `parameters` 写入任何分层键，保持与历史运行逐字节一致的漂移可比性（沿用 `selection_policy` 先例）。
- **强制声明**：分层结果内嵌 `long_short`（A 股不可做空，top–bottom 是统计差非可交易空仓）、`bias_disclosure`（不扣幸存者/ST/停牌/行业时点偏差）、`method`（每层独立等权多头、T+1、含费含税）和 `position_target_policy`（当前为 `explicit_zero_exit_v2`）。
- **Decimal 全程精度**：曲线/拼接/边界均使用 `Decimal`，不做 float 金融精度断裂。
- **抽纯函数**：分桶 `partition_universe_by_factor` 与拼接 `stitch_equity_curves` 为零依赖纯函数，可独立单测。

拒绝项：

- 分层**不落独立 `backtest_run`**：改为内存计算 + `parameters` 内嵌，避免污染运行列表、破坏漂移比较键。
- `split_factor` 不独立成必填显式分析入口：作为 `quantile_backtest` 子参数，随主回测按需触发。

## 金融正确性不变量

- 层 0 = 最低因子值（bottom），层 `n-1` = 最高（top）；各层互斥且覆盖全排名 universe。
- 每个层组合是完整目标组合：离开上期桶的证券必须收到 `target_weight=0`，当期空桶必须清空上期持仓；互斥性不仅适用于信号集合，也必须适用于 T+1 执行后的非零持仓。
- 拼接第一窗从 `initial_cash` 起，后续窗 scale 接续（**本金不重置**），故组合总收益 = 各窗收益乘积 − 1。
- 重叠窗：拼接拒绝，但各窗独立结果仍可用。
- A 股不可做空：top–bottom 是 `layers[-1].total_return − layers[0].total_return`，仅统计差。
- 成本已在主引擎逐日计入；分层层组合与拼接只重排净值点、再聚合换手/佣金，不重复计成本。

## 实现要点

### `backend/app/backtest/quantile.py`（纯函数）
- `partition_universe_by_factor(factor_values_by_date, decision_dates, n_layers, *, max_assets_per_layer=None)`：升序稳定排序、确定性分桶；空日期返回固定 `n_layers` 个空桶，使编排层能显式退出全部旧成员；`max_assets_per_layer` 截断到各桶领先（最低因子值）成员。

### `backend/app/backtest/quantile_result.py`（数据结构 + 序列化）
- `QuantileBacktestMeta`（split 因子/版本/n_layers/max_assets_per_layer/layer_bounds）。
- `QuantileLayerResult`（layer_index/quantile_low/high/universe_size/asset_count/top_asset_symbols/equity_curve/total_return/...）。
- `QuantileBacktestResult`（layers + long_short_spread_return + spread_curve + declarations）。
- `quantile_result_to_json`：`Decimal → str` 序列化，供 `parameters` JSON。
- `quantile_result_to_read`：转 API 读模型 `QuantileBacktestResultRead`。

### `backend/app/backtest/curve_stitch.py`（纯函数）
- `WindowCostSummary(commission, traded_notional)`、`CombinedEquityCurve(...)` dataclass。
- `stitch_equity_curves(window_curves, window_costs, initial_cash)`：按首日期排序、首窗从 `initial_cash` 起、后续窗 scale 接续、**重叠窗 raise ValueError**；再聚合夏普/回撤/换手/佣金。
- `window_cost_summary_from_trades(trades, commission)`：从引擎成交行构造成本摘要。

### `backend/app/services/backtest.py`（分层执行集成）
- `PersistedBacktestResult.quantile` 字段。
- `run()` 新增 `quantile_layer_signals` / `quantile_meta` 参数；保存主结果后调 `_maybe_run_quantile_layers`。分层分支按自身全量证券集合独立读取可信未复权行情、公司行为和交易日历，并做前后双快照校验，快照写入 `parameters.quantile_data_snapshot`；失败仅写 `quantile_backtest_error`，不阻断主 run。
- `_run_quantile_layers`：逐层构造 `DailyEventBacktestEngine` 跑层组合，用 `BacktestPerformanceAnalyzer().analyze(run=_QuantileFakeRun(initial_cash), ...)` 算指标；取 top/bottom 层 equity 造 `spread_curve` 与 `spread_return`。

### `backend/app/services/strategy_research.py`（编排）
- `_build_quantile_layer_signals(db, *, quantile_backtest, universe, invested_weight, start_date, end_date, decision_dates)`：加载 split 因子 → 构造 `factor_values_by_date` → `partition_universe_by_factor` → 每层维护上一决策日成员，先为离桶证券生成零权重退出目标，再为当前桶生成等权目标 → `QuantileBacktestMeta`（含按全样本经验分位估的 `layer_bounds`）。
- `_build_combined_curve(results, initial_cash)`：遍历各窗 `persisted.result.equity_curve` 与 trades → `WindowCostSummary` → `stitch_equity_curves`。
- `run_rolling_factor_selection`：新增 `quantile_backtest` 参数透传；`RollingBacktestResult` 包装（overlap 时 `combined=None` 并写 `stitch_status` 声明）。

### `backend/app/schemas/strategy.py`（API 契约，向后兼容）
- `QuantileBacktestRequest`（split_factor_name/version/n_layers 默认5 ge2 le20/max_assets_per_layer/weighting_mode）；当前实现只支持 `equal_weight_fully_invested`，其他权重模式在 schema 层 fail-fast，禁止静默忽略公共参数。
- `FactorSelectionBacktestRequest` 与 `RollingFactorSelectionBacktestRequest` 各加可选 `quantile_backtest`。
- 读模型：`QuantileLayerRead`、`QuantileBacktestResultRead`、`CombinedEquityCurveRead`、`RollingBacktestResultRead`（windows + combined + declarations）。
- `FactorSelectionBacktestResult` 加可选 `quantile`。

### `backend/app/api/routes/strategy_research.py`
- 单次/批量接口透传 `quantile_backtest`，响应带 `quantile`。
- 滚动接口 `response_model` 改为 `RollingBacktestResultRead`，构造 `windows`/`combined`/`declarations`。

## 测试

- `backend/tests/test_backtest_quantile_curve.py`：纯函数 `partition_universe_by_factor` 与 `stitch_equity_curves` 的确定性、互斥覆盖、空日期、截断、本金接续、重叠拒绝。
- `backend/tests/test_strategy_research_service.py`：扩展——分层各层信号/多空差在 fake 体系下的透传；曲线拼接本金接续与重叠拒绝；默认 `quantile_backtest=None` 逐字节不变回归锁定（`kwargs["quantile_layer_signals"]`/`quantile_meta` 为 None）。

## 2026-10-25 P0 修正：显式退出与独立行情范围

首版实现经金融与架构复核发现两个 P0：每层只向当期桶成员发送正权重，导致离桶旧仓被引擎继续持有；同时分层引擎复用了主策略 top-N 信号的行情/日历范围，层内其他证券会缺少执行日历并被静默记录为 `quantile_backtest_error`。这两个问题不影响主策略回测和 rolling `combined` 主曲线，但会使首版多决策日分层曲线、换手、费用、单调性与 top–bottom spread 不可信。

修正后的口径：

- 每层独立维护上一决策日成员，离桶证券发 `target_weight=0`；缺少 split 因子值的决策日产生固定数量空桶并清仓。
- 分层 universe 沿用主请求的 `allowed_security_types`，缺失证券主数据或不在允许类型集合内的标的不进入任何层；分层分支再独立按过滤后的全部信号证券加载可信未复权行情、公司行为与交易日历，并做运行前后快照一致性校验；量化分支失败仍不回滚已完成的主回测。
- 新结果声明 `position_target_policy=explicit_zero_exit_v2`，并保存 `parameters.quantile_data_snapshot`。缺少该声明的旧多决策日分层结果视为失效，必须重跑；不删除或改写对应主回测。
- 本机 `data/research.duckdb` 只读盘点未发现已保存的 `quantile_backtest` / `quantile_backtest_error` 运行，因此无需本地数据迁移。

回归覆盖成员轮换的零权重退出、空桶清仓、T+1 后持仓与最新桶一致、量化证券超出主策略信号集合时的独立行情加载，以及量化公司行为失败不影响主回测。

## 已知限制（沿用在位声明，不扣偏差）

- `security_status_event` = 0 行：停牌只能靠「当日无 bar」推断。
- `security_industry_history` = 0 行：行业中性化在拿到时点标签前不可信。
- 公司行为事件会计未实现：命中除权除息的区间 fail-closed。
- 上述三项与幸存者偏差一起构成样本偏差，`bias_disclosure` 已显式声明，分层与回测净收益均不可表述为已校正偏差的策略收益。
