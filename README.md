# Stock Master

Stock Master 是一个面向 A 股、ETF 和后续量化研究场景的个人量化平台，按长期维护和扩展来设计。

当前项目已经从 **可信日线数据中心** 推进到因子、回测、组合、风控、模拟盘和运维观测的最小闭环。V4 单策略研究合同与可复现回测切片已落地，运行记录可还原完整输入和版本。不依赖某一个单一数据源，而是通过多源导入、原始留痕、质量校验和人工修复，形成可供策略、回测和组合执行使用的本地可信数据底座。

V4 研究接口支持批量运行、样本外合同、按指定交易所开市日生成滚动窗口规划与执行、基准与跨运行比较。`POST /api/v1/strategy-research/rolling-backtests` 会按窗口逐个创建不可变训练产物（`strategy.training_artifact`，含训练期因子快照与 SHA-256）并生成独立 `backtest_run`，每个运行通过 `training_artifact_id` 绑定到对应窗口产物。样本外报告只聚合服务端既有运行与绩效事实，保留失败窗口并禁止对重叠窗口直接复利；训练和选参仍由调用方完成，训练产物验证开关默认未验证，因此当前不会把滚动执行宣传为全自动 walk-forward。

## 当前状态

项目当前已完成 **V6.1 只读运维工作台** 的阶段性收口，并形成 V5 最小公共 paper 操作闭环。V5.1 已开放幂等的手工模拟组合草案；V5.2 已交付风险前数据核对、不可变 paper 账户净值快照、服务端 20 日 ADV（至少 15 个观测）和绑定草案/账户/净值/readiness/ADV/政策哈希的正式风险评估；V5.3 已开放五分钟有效、可撤销的人工确认，以及一次性原子消费、卖先买后、全批预演、幂等模拟执行审计，并通过执行日 `market.daily_bar` 与 `AShareTradabilityPolicy` 接入 A 股可交易性检查（确认与执行两阶段均强制，缺失 bar 或被可交易性策略禁止的方向以 `status=rejected` 落库；执行批次哈希包含 `execution_day_bar_hash`）。执行固定使用本地 full-fill paper 模型，不会向券商或交易所发送订单。V6 写侧重跑执行仍需另行完成权限、审计、幂等和人工确认设计；V7 实盘只读与受控交易尚未开始。

已有：

- Python/FastAPI/DuckDB/SQLAlchemy 基础工程。
- `raw -> market -> factor -> research/backtest -> portfolio/risk -> trade` 分层闭环。
- 参考数据同步、真实采集、`raw -> market` 构建统一入口。
- HiThink、东方财富补充源、CSV、数据湖和近期缺口补采的导入/维护入口。
- 基础价量、财务、估值、行业、资金流、拥挤度、情绪、宏观、政策、盈利预期和复合因子首版。
- 横截面处理、IC/Rank IC、forward diagnostics、top quantile turnover 等因子统计报告。
- 回测、绩效归因、因子选股研究入口、组合构建、下单前风控。
- 模拟盘订单、成交、现金和持仓闭环。
- Vue3 多页面工作台，左侧路由导航覆盖总览、行情、质量、因子、回测、组合、模拟盘和运维；行情页使用本地可信日线绘制红涨绿跌 K 线与成交量副图，明细表作为折叠审计视图；因子页支持手动选择与参数配置，模拟盘支持数据库账户下拉选择。

核心 API 面：

- 价格与价值研究：`POST /api/v1/strategy-research/price-value/evaluate`；前端菜单“估值研究”（`/price-value`）。输入三情景 FCFE 总额、稀释股数、时点、费用和安全边际等显式研究假设，服务端读取指定日期可信未复权行情，返回情景价值、含费成本上限、最高报价、复核/阻断原因和输入合同哈希。固定 `research_only=true`、`pit_verified=false`、`execution_eligible=false`，不写入数据库、不生成权重或订单。字段与范围见[策略规范](aiDoc/strategy/20260922-1314-price-value-margin-strategy.md)。
- 价格价值假设回测：`POST /api/v1/strategy-research/price-value/backtests`、`GET /api/v1/strategy-research/price-value/backtests/{run_id}`；同页新增版本化假设历史模拟。单只沪深主板普通股与现金，收盘生成信号、次日开盘按实际整手数量和费用复核买价，达到基准价值或论点失效触发模拟退出，未成交退出持续重试。不自动加仓，未用预算保留现金。运行写入 `strategy.backtest_*` 研究记录，返回净值、现金、成交、决策日志和合同指纹；当前修订行情与手工假设不构成历史时点认证或样本外验证，不创建 paper 或真实订单。遇公司行为、历史 ST/状态未知或缺失可信行情/涨跌停数据时拒绝运行。
- 全市场相对估值研究：`GET /api/v1/strategy-research/price-value/screen/latest` 返回上一已完成交易日的 PE/PB/披露 ROE 人工初筛、沪深北逐项覆盖、自动同步状态、逐股缺失原因及原始批次指纹。ROE 只使用当晚核对的财报加权平均值，按沪深北各所至少 80% 覆盖选最新可比主报告期；已有更新财报时同时展示最新 ROE，页面阈值只比较主报告期。`GET /api/v1/strategy-research/price-value/profit-status` 分别读取价值研究回测的假设盈亏及模拟账户净值状态。没有回测记录或可核验投入基线时返回不可计算，不将缺失解释成零收益。页面 `/price-value` 可自行设置筛选阈值、查看覆盖原因和导出报告。9 月 23 日首轮实际晚间采集成功，但行业与 ST 状态未核实使通过名单仍为零；页面默认展示带逐股缺口的原始指标供人工查看；名单仅供人工复核，无默认买入阈值、FCFE 安全边际、订单或历史全市场盈利认证。
- 估值研究页使用顺序：先看每日全市场只读初筛及数据状态，再填入自己的三情景 FCFE 预测研究单股价格，最后在核对假设历史可得时间后运行单股假设回测。页面将收益记录与初筛名单分开展示；数据未备齐时说明原因，不把空名单或缺失利润解释为零收益。

- 健康检查：`GET /health`
- 行情与参考数据：`GET /api/v1/securities`、`GET /api/v1/securities/{symbol}/lifecycle`、`GET /api/v1/daily-bars/{symbol}`、`GET /api/v1/daily-bars/{symbol}/chart`（最新可消费日线窗口，仅含 `trusted/manual_fixed` 且非可疑记录）、`GET /api/v1/daily-bars/missing`、`GET /api/v1/trading-calendar`
- 数据质量与修复：`GET /api/v1/data-quality/issues`、`PATCH /api/v1/data-quality/issues/{issue_id}/status`、`GET /api/v1/daily-bar-source-compares`、`GET /api/v1/manual-corrections`
- 复权与公司行为：`GET /api/v1/corporate-actions`、`GET /api/v1/adjustment-factors`
- 因子：`GET /api/v1/factors/definitions`、`GET /api/v1/factors/runs`、`GET /api/v1/factors/values`、`GET /api/v1/factors/profiles`、`POST /api/v1/factors/batch-profiles`（对全部活跃因子批量运行横截面诊断并生成 IC/Rank IC 证据，单因子失败隔离，整批运行写入 `system.job_log`）、`GET /api/v1/factors/batch-profiles/summary`（按因子聚合已持久化的 IC 证据，缺失 horizon 返回空而非 0）
- 研究与回测：`POST /api/v1/strategy-research/rolling-windows`、`POST /api/v1/strategy-research/rolling-backtests`、`POST /api/v1/strategy-research/factor-selection/backtests`、`POST /api/v1/strategy-research/factor-selection/backtests/batch`、`GET /api/v1/backtests/runs`、`GET /api/v1/backtests/runs/{run_id}`、`GET /api/v1/backtests/runs/{run_id}/trades`、`GET /api/v1/backtests/runs/{run_id}/equity`、`GET /api/v1/backtests/runs/{run_id}/positions`、`GET /api/v1/backtests/runs/{run_id}/performance`、`POST /api/v1/backtests/runs/{run_id}/performance`、`GET /api/v1/backtests/runs/{run_id}/benchmark`、`POST /api/v1/backtests/runs/compare`、`POST /api/v1/backtests/runs/drift`、`POST /api/v1/backtests/runs/validation-report`；研究信号可使用复权行情，成交统一使用可信未复权价格，跨公司行为区间在事件会计落地前明确拒绝。`out_of_sample` 运行强制训练期早于测试期且只在测试区间取因子并回测；验证报告只读聚合可比运行并保留失败窗口，不对重叠窗口复利；`rolling-windows` 生成带 `window_id` 的规划窗口，`rolling-backtests` 会按窗口逐个创建不可变训练产物（`strategy.training_artifact`）并生成独立 `backtest_run`，每个运行通过 `training_artifact_id` 绑定到对应窗口产物
- 组合草案：`POST /api/v1/portfolio/plans`（必须提供 `Idempotency-Key`）、`GET /api/v1/portfolio/plans/{plan_id}`、`GET /api/v1/portfolio/plans/{plan_id}/risk-readiness?paper_account_id=...`；输入为操作者手工模拟快照，风险前核对也固定标记 `execution_allowed=false`、`risk_evaluated=false`、`orders_created=false`。`PortfolioConstraintsRequest` 支持 `enforcement_mode`（`proportional_scale` 等比缩放或 `score_ranked_trim` 按分数择优截断）、`extra_symbol_caps`（单标的额外上限）、`cash_reserve_rate` 等既有约束，以及 `risk_budget`（含 `max_industry_weight`、`max_style_exposure`、`max_tracking_error`、`max_portfolio_volatility`、`benchmark`）；组合构建时把 `max_industry_weight` 与风险预算中的行业上限取较小值生效，权重削减原因写入 `PortfolioTarget.reasons`。风格暴露、跟踪误差和波动率约束已暴露 schema 与持久化，但计算与强制尚未实现。约束口径写入 `portfolio_plan.parameters` 以支持回放与审计，正式风控从同一参数构造 `RiskLimits`，确保事前检查与组合构建同源。
- Paper 账户：`GET /api/v1/paper-accounts` 提供本地模拟账户选择；`GET /api/v1/paper-accounts/{account_id}/ledger-audit` 只读核对订单、成交、执行批次、现金与持仓事实并返回差异，不自动修复；`POST /api/v1/paper-accounts/{account_id}/equity-snapshots`（必须提供 `Idempotency-Key`）、`GET /api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}`；净值快照仅使用服务端现金、全量持仓和 exact-date 可信未复权收盘价，回撤口径为“自监控开始以来”
- 正式风控：`POST /api/v1/portfolio/plans/{plan_id}/risk-evaluations`（必须提供 `Idempotency-Key`）；请求只引用候选 paper 账户和净值快照，服务端绑定 readiness、ADV 和固定风险政策，响应固定 `execution_allowed=false`、`orders_created=false`
- 模拟执行确认：`POST /api/v1/risk-evaluations/{risk_check_id}/confirmations`、`GET /api/v1/paper-execution-approvals/{approval_id}`、`POST /api/v1/paper-execution-approvals/{approval_id}/revoke`（写操作必须提供 `Idempotency-Key`）；确认绑定完整评估和规范化订单批次哈希，五分钟后失效，且响应固定 `execution_allowed=false`、`orders_created=false`。确认阶段使用与回测一致的 `AShareTradabilityPolicy` 读取执行日 `market.daily_bar` 做预飞，缺失 bar 或被可交易性策略禁止的方向会以 `PaperApprovalConflict` 拒绝，不会写入确认
- 模拟执行：`POST /api/v1/paper-execution-approvals/{approval_id}/executions`（必须提供 `Idempotency-Key`）、`GET /api/v1/paper-executions/{execution_id}`、`GET /api/v1/paper-execution-approvals/{approval_id}/execution`；请求只确认 approval hash，服务端重新验证全部事实和代码版本，保守执行同日买入不可卖，并在单事务中消费确认、按卖先买后顺序写入本地模拟订单、成交、现金和持仓；按 approval 回读可恢复超时或页面刷新后的结果。执行日通过 `market.daily_bar`（`adjust_type=none`）执行 `AShareTradabilityPolicy`，与回测共用同一份可交易性规则；任一订单被 `missing_execution_day_bar`、`buy_blocked_by_execution_day_tradability` 或 `sell_blocked_by_execution_day_tradability` 拒绝时整批以 `status=rejected` 落库，`execution_policy_hash` 显式包含 `execution_day_bar_hash` 以绑定执行日行情快照
- 运维工作台：`GET /api/v1/operations/overview`、`GET /api/v1/operations/alerts`、`GET /api/v1/operations/failures`、`GET /api/v1/operations/failures/{failure_type}/{failure_id}`、`GET /api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan`

## 技术栈

- Python 3.13
- FastAPI
- DuckDB（单库存储 + 本地 Parquet 温层）
- SQLAlchemy 2.x
- Pandas / Polars
- Backtrader
- Plotly
- Vue3

## 文档入口

项目文档入口见：

- [AI 协作参考入口](aiDoc/README.md)
- [V1 可信日线数据中心架构](aiDoc/relations/trusted-daily-data-center.md)
- [日线数据字段契约](aiDoc/data/daily-bar-contract.md)
- [数据库 Schema 规划](aiDoc/modules/schema-plan.md)
- [路线图](aiDoc/plans/roadmap.md)

## 阶段规划

1. **V1 可信日线数据中心**：多源导入 + DuckDB + 原始留痕 + 质量校验 + 人工修复。
2. **V2 复权、ETF 和指数增强**：复权因子、交易日历、ETF/指数扩展。
3. **V3 因子工厂**：技术面、量价、趋势、资金面等因子沉淀。
4. **V4 策略与回测**：多因子评分、ETF 轮动、股票选股、绩效分析。
5. **V5 组合、风控和模拟盘**：组合构建、下单前风控、模拟订单、成交、现金和持仓。
6. **V6 可视化和运维**：运维概览接口、数据质量、任务、因子、回测和模拟盘状态。
7. **V7 实盘只读与受控交易**：真实账户只读同步、人工确认、风控边界和应急开关。

## 快速开始

项目使用 uv 管理 Python 和依赖：

```bash
cd backend
cp .env.example .env
uv sync --extra dev
# 启动 API 服务（表结构在首次启动时自动创建）
uv run uvicorn app.main:app --reload
# 或运行单次日更闭环
uv run python scripts/scheduler/run_daily_loop.py
```

在 `backend/.env` 配置 `HITHINK_FINANCE_API_KEY` 后，API 启动会立即在后台同步日线，
之后每次同步结束等待一小时再检查。首次自动同步使用供应商历史全量包建立基线，
后续扫描近期包，只新增缺失或修订的原始记录；停机超过近期包覆盖的交易日范围时自动回补历史。
首次下载和入库可能较久，API 可继续提供已有数据；同步结束后重新查询行情即可看到更新。
上海时间 18:00 前不发布当天日线，实际更新以供应商已发布且通过现有校验的数据为准。
自动任务更新未复权日线，不自动重建前复权、因子、回测或交易。

价值初筛另有同一 API 进程内的晚间任务：上海交易日 21:00 后采集当前东方财富估值及财报 ROE 的原始响应分页到 `raw.import_batch` / `raw.vendor_snapshot`，按沪深北分别核对证券、供应商市场码、可信日线、0.01 元收盘价差及有效 PE/PB、披露 ROE 覆盖，再发布可追溯的当晚研究观察值。跨午夜采集不得发布为前日观察。研究估值行带 `_research_batch_id`，通用估值因子和市值读取默认排除；晚间任务不写通用 `roe_ttm/v1` 因子。供应商 f9 只称周期未核验的 PE，不写作 PE(TTM)。只有当日自动日线同步成功才尝试发布；同日晚间失败每 30 分钟重试。00:00 生成上一交易日全市场只读报告，启动及白天每小时补查变化。停机跨日后不能将新的当前报价倒填旧日；缺失时报告阻断。东方财富接口不提供可证明的供应商 as-of 时点，报告固定 `pit_verified=false`，不得用于历史全市场回测收益或自动交易。`PRICE_VALUE_INPUT_PREP_ENABLED` 和 `PRICE_VALUE_SCREEN_ENABLED` 可分别关闭任务。

`AUTO_DATA_SYNC_ENABLED=false` 可关闭；`AUTO_DATA_SYNC_INTERVAL_SECONDS` 可调整间隔
（默认 3600 秒，最小 60 秒）。缺少 API key 时记录警告并跳过，失败记录到现有作业日志
`auto_data_sync` 并在下个周期重试。DuckDB 服务使用单个 worker，避免同时运行其他写库调度脚本；
正常关闭会等待正在执行的同步完成，以免中断写库。

常用验证命令：

```bash
uv run --extra dev ruff check app tests alembic
uv run --extra dev pytest
uv run alembic current
```

## 目录结构

```text
backend/
├── app/            # Python/FastAPI 后端应用代码
├── alembic/        # 数据库迁移
├── data/           # 本地数据和数据湖文件
├── scripts/        # 后端导入、因子、维护和调度入口
├── sql/            # SQL 资源
├── tests/          # 后端测试
├── pyproject.toml  # Python 项目配置
└── docker-compose.yml

aiDoc/
├── data/           # 数据契约、导入、质量校验
├── modules/        # 模块索引、后端规则、schema 规划
├── plans/          # 路线图和阶段计划
├── relations/      # 仓库概览、系统关系、架构设计
├── research/       # 因子、策略和研究方法设计
└── memory/         # 跨任务稳定记忆

frontend/
└── src/            # Vue3 前端工作台
```

## 核心原则

- 任何外部数据源默认不可靠。
- 所有来源先进入 `raw` 原始层。
- 数据获取本身可能不完整：单一数据源可能缺新 ETF、指数或特殊品种，允许引入补充数据源，但必须保留来源、批次和原始载荷。
- 清洗、校验、对账、修复后才能进入 `market` 可信层。
- 因子、策略、回测和 API 查询只消费 `market` / `factor` 可信层。
- 人工修复必须记录审计信息。
- 实盘写操作必须有人工确认、风控边界和可追溯审计；当前默认只推进模拟盘和只读边界。
