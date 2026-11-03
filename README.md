# Stock Master

> 本文提供单机开发环境的安装、启动和检查入口。命令从仓库根目录执行；后端运行目录固定为 `backend/`，使 `.env` 与相对数据路径保持一致。

## 1. 项目用途

Stock Master 是一个面向 A 股、ETF 及后续量化研究场景的**个人量化平台**，按长期维护和扩展设计。

项目采用 `raw → market → factor → research/backtest → portfolio/risk → trade` 的分层数据链路，目标是构建一个本地可信的数据底座，并在其上提供因子研究、策略回测、组合构建、下单前风控与持仓推演闭环。

**项目边界**：本项目**只用于选股研究与持仓管理**，不连接券商，不做实盘自动化交易，不发送任何真实订单。系统产出止于「研究结论 + 选股名单 + 组合持仓方案 + 风险预算 + 持仓推演」，实际下单由人工在外部券商终端完成。

当前进度（依据 `README.md` 历史记录与 `aiDoc/`）：

- V1 可信日线数据中心：多源导入、原始留痕、质量校验、人工修复。
- V2 复权 / ETF / 指数扩展。
- V3 因子工厂：基础价量、财务、估值、行业、资金流、拥挤度、情绪、宏观、政策、盈利预期与复合因子。
- V4 策略与回测：单策略研究合同、可复现回测、滚动窗口与样本外报告。
- V5 组合、风控与持仓推演：组合草案、风险预算、paper 账户净值快照、正式风控评估、人工确认与模拟执行。
- V6 可视化与运维：Vue3 工作台 + 运维概览/告警/失败重跑计划接口。

核心工程原则：

- 任何外部数据源默认不可靠，一律先进入 `raw` 原始层。
- 清洗、校验、对账、修复后才能进入 `market` 可信层。
- 因子、策略、回测与 API 查询只消费可信层，不直接读外部接口或 `raw`。
- 人工修复必须写审计记录，不允许静默覆盖。
- 系统只产出选股与持仓建议，所有真实下单由人工在外部终端完成；平台内的模拟执行仅用于持仓推演。

技术栈：Python 3.13、FastAPI、SQLAlchemy 2.x、DuckDB（单库 + 本地 Parquet 温层）、Pandas/Polars、Backtrader、Plotly、Vue 3 + TypeScript + Vite。

## 2. 安装步骤

准备 Python 3.13、uv 和 Node.js（版本见 `frontend/.nvmrc`），然后执行：

```powershell
uv sync --project backend --extra dev --extra data
# 仅首次创建；保留已有配置
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
cd frontend
npm ci
cd ..
```

Linux/macOS 创建配置可用 `test -f backend/.env || cp backend/.env.example backend/.env`。
在 `backend/.env` 中配置所需数据源凭据。可选依赖组见 `backend/pyproject.toml`：`data`、`backtest`、`viz`、`dev`、`all`；相关版本由 `backend/uv.lock` 记录。

## 3. 运行方式

### 后端服务

在一个终端中执行：

```powershell
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

服务地址为 `http://127.0.0.1:8000`，健康检查为 `GET /health`，接口文档为 `/docs`。
入口 `backend/app/main.py` 启动时初始化主 DuckDB、研究 DuckDB 和 SQLite 持仓账本，并启动行情同步、估值输入准备和初筛三个后台 worker。数据源凭据与开关由 `backend/.env` 控制。

### 前端工作台

在另一个终端中，从仓库根目录执行：

```powershell
cd frontend
npm run dev
```

默认地址为 `http://127.0.0.1:5173`；若端口被占用，以 Vite 输出为准。
`frontend/vite.config.ts` 将 `/api` 和 `/health` 代理至 `http://127.0.0.1:8000`，可通过 `STOCK_MASTER_API_URL` 覆盖。生产构建执行 `npm run build`。

### 日常与离线任务

`backend/scripts/` 下为真实的任务入口目录，主要分组：

- `scheduler/`：`run_daily_loop.py`、`run_hithink_scheduler.py`、`run_recent_data_completion_scheduler.py`
- `factors/`：`run_all_factors.py`、`run_basic_factors.py`、`run_financial_factors.py`、`run_valuation_factors.py`、`run_industry_factors.py`、`run_capital_flow_factors.py`、`run_sentiment_factors.py`、`run_macro_factors.py`、`run_policy_event_factors.py`、`run_earnings_revision_factors.py`、`run_crowding_factor.py`、`run_composite_factor.py`、`run_factor_cross_section.py`、`run_batch_profiles.py`、`classify_factor_pool.py`、`run_lake_factors.py`
- `ingest/`：`hithink/`、`eastmoney/`、`csv/` 及 `_rebuild_curated_qfq_supplement.py`
- `maintenance/`：`apply_daily_bar_correction.py`、`backfill_missing_kline.py`、`build_curated_layer.py`、`rebuild_qfq.py`
- `lake/`：`build_data_lake.py`
- `repair/`：`isolate_price_value_roe_factor.py`

定时调度由 `backend/app/scheduler/` 内的 APScheduler worker 承载，随 API 进程启动。

### 校验与 CI

- 检查命令唯一维护在 `scripts/ci_check.py`，本地和 `.github/workflows/ci.yml` 共用。
- 后端对 `app/`、`tests/`、`backend/scripts/` 和统一检查脚本执行 Ruff，再运行一次完整 pytest，自动包含 `tests/` 下的全部测试。前端执行 package.json 中已配置的 `test` 脚本（未配置则跳过），再执行类型检查/构建；任一步失败立即返回非零状态。
- 从仓库根目录执行（Windows、Linux、macOS 通用）：

  ```powershell
  uv run --project backend --extra dev python scripts/ci_check.py
  # 按范围运行；--list 仅展示命令
  uv run --project backend --extra dev python scripts/ci_check.py --scope backend
  uv run --project backend --extra dev python scripts/ci_check.py --scope frontend
  ```

- Bash 兼容入口 `bash scripts/ci-check.sh` 转发到同一脚本，也接受 `--scope` 和 `--list`。

## 4. 使用示例

> 下列接口路径均来自 `backend/app/api/routes/` 的真实路由文件；主机地址、端口与完整请求体字段请按部署环境补齐后再作为示例使用。

### 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 行情与参考数据

路由文件：`backend/app/api/routes/securities.py`、`daily_bars.py`、`trading_calendar.py`

- `GET /api/v1/securities`
- `GET /api/v1/securities/{symbol}/lifecycle`
- `GET /api/v1/daily-bars/{symbol}`
- `GET /api/v1/daily-bars/{symbol}/chart`
- `GET /api/v1/daily-bars/missing`
- `GET /api/v1/trading-calendar`

### 数据质量与人工修复

路由文件：`data_quality.py`、`source_compares.py`、`manual_corrections.py`、`corporate_actions.py`、`adjustment_factors.py`

- `GET /api/v1/data-quality/issues`
- `PATCH /api/v1/data-quality/issues/{issue_id}/status`
- `GET /api/v1/daily-bar-source-compares`
- `GET /api/v1/manual-corrections`
- `GET /api/v1/corporate-actions`
- `GET /api/v1/adjustment-factors`

### 因子

路由文件：`backend/app/api/routes/factors.py`

- `GET /api/v1/factors/definitions`
- `GET /api/v1/factors/runs`
- `GET /api/v1/factors/values`
- `GET /api/v1/factors/profiles`
- `POST /api/v1/factors/batch-profiles`
- `GET /api/v1/factors/batch-profiles/summary`（返回评估窗口统一的 `code_version` 与 `code_version_count`；混合版本时统一版本为 `null`；另返回 1/5/20 日 Rank IC 各自的非空观测数）
- `POST /api/v1/factors/pool-classifications`（因子池分级：端点、PIT、生命周期、证据资格与代码版本一致性闸门；混合、缺失或占位代码版本不得进入核心池）

### 研究与回测

路由文件：`strategy_research.py`、`backtests.py`

- `POST /api/v1/strategy-research/rolling-windows`
- `POST /api/v1/strategy-research/rolling-backtests`（可选 `selection_policy: "training_ic_v1"` 启用训练窗自动选参——逐窗按预注册 RankIC 门槛从候选池选因子/方向并写入 `training_artifact`，测试窗纯样本外执行；见 `aiDoc/plans/20261024-1522-training-window-selection-plan.md`。可选 `quantile_backtest` 在每个测试窗内做分层回测。返回 `RollingBacktestResult`：各窗 `windows` + 跨窗拼接的 `combined` 组合净收益曲线（重叠窗 fail-closed 拒绝拼接、各窗仍独立可用）+ 非可交易声明）
- `POST /api/v1/strategy-research/factor-selection/backtests`（支持 `rebalance_rule: daily|weekly|monthly|every_n_trading_days` + `rebalance_interval`，以及可选 `portfolio_constraints`：单标的上限/行业上限/现金留存率/证券类型预算，经 PortfolioBuilder 与 paper 链路同源。可选 `quantile_backtest`：`split_factor_name/version` + `n_layers`(2–20) + `max_assets_per_layer` 按 split 因子在决策日横截面分桶；当前仅接受 `weighting_mode=equal_weight_fully_invested`，其他模式 fail-fast，逐桶等权多头跑净收益曲线与 top–bottom 统计收益差；换层以 `explicit_zero_exit_v2` 显式退出旧成员，分层分支独立读取并双快照校验全量证券行情，结果内嵌主运行 `parameters.quantile_backtest` / `quantile_data_snapshot` 并随响应返回 `quantile`）
- `POST /api/v1/strategy-research/factor-selection/backtests/batch`
- 价格价值研究：`POST /api/v1/strategy-research/price-value/evaluate`
- 价格价值假设回测：`POST /api/v1/strategy-research/price-value/backtests`、`GET /api/v1/strategy-research/price-value/backtests/{run_id}`
- 全市场相对估值初筛：`GET /api/v1/strategy-research/price-value/screen/latest`、`GET /api/v1/strategy-research/price-value/profit-status`
- 回测运行：`GET /api/v1/backtests/runs`、`GET /api/v1/backtests/runs/{run_id}`、`.../trades`、`.../equity`、`.../positions`、`.../performance`（strategy 产物存独立 `data/research.duckdb`，见 `aiDoc/relations/20261024-1427-research-database-split.md`）
- 基准与对比：`GET /api/v1/backtests/runs/{run_id}/benchmark`、`POST /api/v1/backtests/runs/compare`、`POST /api/v1/backtests/runs/drift`、`POST /api/v1/backtests/runs/validation-report`
- 回测最终持仓（组合草案预填源）：`GET /api/v1/backtests/runs/{run_id}/final-holdings`

### 组合、风控与模拟盘

路由文件：`portfolio.py`、`paper_accounts.py`、`approvals.py`、`position_books.py`

- 组合草案：`POST /api/v1/portfolio/plans`（需 `Idempotency-Key`）、`GET /api/v1/portfolio/plans/{plan_id}`、`GET /api/v1/portfolio/plans/{plan_id}/risk-readiness`
- 草案偏离检测：`GET /api/v1/portfolio/plans/{plan_id}/drift?position_book_id=|paper_account_id=&threshold=`（只读建议，不生成订单）
- 正式风控评估：`POST /api/v1/portfolio/plans/{plan_id}/risk-evaluations`（需 `Idempotency-Key`）
- Paper 账户：`GET /api/v1/paper-accounts`、`GET /api/v1/paper-accounts/{account_id}/ledger-audit`、`GET /api/v1/paper-accounts/{account_id}/positions`、`GET /api/v1/paper-accounts/{account_id}/performance`
- 净值快照：`POST /api/v1/paper-accounts/{account_id}/equity-snapshots`（需 `Idempotency-Key`）、`GET /api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}`
- 真实持仓账本（独立 SQLite，手工记账）：`GET/POST /api/v1/position-books`、`PATCH /api/v1/position-books/{book_id}`、`GET /api/v1/position-books/{book_id}/holdings`、`GET /api/v1/position-books/{book_id}/trades`、`POST /api/v1/position-books/{book_id}/trades`（需 `Idempotency-Key`）、`DELETE /api/v1/position-books/{book_id}/trades/{trade_id}`
- 人工确认：`POST /api/v1/risk-evaluations/{risk_check_id}/confirmations`、`GET /api/v1/paper-execution-approvals/{approval_id}`、`POST /api/v1/paper-execution-approvals/{approval_id}/revoke`
- 模拟执行：`POST /api/v1/paper-execution-approvals/{approval_id}/executions`、`GET /api/v1/paper-executions/{execution_id}`

### 运维工作台

路由文件：`operations.py`

- `GET /api/v1/operations/overview`
- `GET /api/v1/operations/alerts`
- `GET /api/v1/operations/failures`
- `GET /api/v1/operations/failures/{failure_type}/{failure_id}`
- `GET /api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan`

### 前端页面

前端路由定义在 `frontend/src/router.ts`，已挂载页面路径：

`/overview`、`/market`、`/factors`、`/price-value`、`/backtests`、`/positions`、`/portfolio`、`/paper`、`/quality`、`/operations`、`/alerts`、`/failures`（`/` 重定向到 `/overview`，未匹配路径同样重定向）。

页面实现在 `frontend/src/pages/`：`MarketPage.vue`、`FactorPage.vue`、`PriceValuePage.vue`、`PositionsPage.vue`、`PaperTradingPage.vue`、`WorkspaceRoutePage.vue` 及其 `components/` 子目录；API 封装在 `frontend/src/api.ts`、`priceValueApi.ts`、`priceValueBacktestApi.ts`、`priceValueScreenApi.ts`。

## 5. 关键配置说明

配置通过 `pydantic-settings` 加载，环境变量文件为 `backend/.env`（模板 `backend/.env.example`），定义在 `backend/app/config/settings.py`。

| 配置项 | 说明 | 代码默认值 |
| --- | --- | --- |
| `APP_NAME` | 应用名（FastAPI title） | `stock-master` |
| `APP_ENV` | 运行环境标识 | `dev` |
| `APP_DEBUG` | 调试开关 | `true` |
| `METADATA_DB_PATH` | 主 DuckDB 路径，派生 `database_url`（`duckdb:///...`） | `data/stock_master.duckdb` |
| `RESEARCH_DB_PATH` | 独立研究 DuckDB 路径 | `data/research.duckdb` |
| `POSITIONS_DB_PATH` | 手工持仓账本 SQLite 路径 | `data/positions.db` |
| `RESEARCH_READ_SOURCE` | 研究输入读取路径（`warm` / `control`） | `warm` |
| `AKSHARE_TIMEOUT_SECONDS` | AkShare 超时 | `30` |
| `HITHINK_FINANCE_API_KEY` | 同花顺金融数据服务密钥（缺失时自动同步记录警告并跳过） | 空 |
| `HITHINK_FINANCE_BASE_URL` | 同花顺服务地址 | `https://fuyao.aicubes.cn` |
| `HITHINK_FINANCE_TIMEOUT_SECONDS` | 同花顺超时 | `30` |
| `HITHINK_DUMP_CACHE_DIR` | 原始包落地目录 | `data/hithink_dumps` |
| `AUTO_DATA_SYNC_ENABLED` | API 启动后的自动日线同步开关 | `true` |
| `AUTO_DATA_SYNC_INTERVAL_SECONDS` | 自动同步检查间隔（最小 60 秒） | `3600` |
| `DATA_LAKE_ROOT` | Parquet 温层根目录 | `data/warm` |
| `RAW_LANDING_ROOT` | 原始落地根目录 | `data/raw_landing` |
| `FACTOR_READS_CURATED` | 价格类因子是否改读 `curated/prices_qfq` Parquet（仅 qfq 生效） | `true`；`.env.example` 显式设为 `false` |
| `PRICE_VALUE_SCREEN_ENABLED` | 估值初筛晚间任务开关 | `true` |
| `PRICE_VALUE_SCREEN_DIR` | 初筛结果输出目录 | `data/price_value_screens` |
| `PRICE_VALUE_INPUT_PREP_ENABLED` | 估值输入准备任务开关 | `true` |
| `EASTMONEY_TIMEOUT_SECONDS` | 东方财富超时 | `15` |
| `EASTMONEY_RETRIES` | 东方财富重试次数 | `2` |
| `XUEQIU_COOKIE` | 雪球 Cookie（可空） | 空 |
| `XUEQIU_TIMEOUT_SECONDS` | 雪球超时 | `15` |

其他约定：

- 字段命名 `snake_case`；证券代码统一 `symbol`；交易日期统一 `trade_date`；复权类型统一 `adjust_type`。
- 核心价格 / 金额 / 比率使用精确定点类型（DuckDB `DECIMAL`、Parquet `DECIMAL`），不使用浮点保存核心金融数据。
- 应用代码版本号由 `settings.source_code_version()` 对 `app/**/*.py` 内容做 SHA-256 生成（形如 `source-<20位十六进制>`），无需手工配置。
- 前端后端地址通过环境变量 `STOCK_MASTER_API_URL` 覆盖（见 `frontend/vite.config.ts`）。
- 上文地址适用于本机开发；其他监听地址和端口由启动参数指定，前端代理目标由 `STOCK_MASTER_API_URL` 指定。

## 6. 常见问题

**Q：首次启动需要做什么？**
A：复制 `backend/.env.example` 为 `backend/.env` 并按需填写；应用启动时 `initialize_database()` 会创建表结构，无需手动建库。

**Q：没有配置 `HITHINK_FINANCE_API_KEY` 会怎样？**
A：自动同步任务记录警告并跳过，失败写入作业日志（作业名 `auto_data_sync`），下个周期重试；其余只读接口不受影响。

**Q：自动同步多久跑一次？**
A：默认每小时检查一次（`AUTO_DATA_SYNC_INTERVAL_SECONDS=3600`，最小 60 秒）。首次用供应商历史全量包建立基线，之后只扫描近期包补缺失或修订；停机超过近期包覆盖范围会自动回补历史。上海时间 18:00 前不发布当天日线。

**Q：如何采集 AKShare/Tushare 的分红日期？**
A：在 `backend/.env` 设置 `CORPORATE_ACTION_EVIDENCE_SYNC_ENABLED=true`；AKShare 需安装 `backend` 的 `data` 可选依赖，Tushare 另需 `TUSHARE_TOKEN`。自动任务每轮独立采集缺日期证券，即使当轮日线同步失败仍可继续；默认每轮 AKShare 10 只、Tushare 至多 1 只，后者还受接口频次限制。手工补采可在 `backend/` 运行 `python -m scripts.ingest.collect_corporate_action_evidence --source akshare --symbol 600519.SH`。结果仅进入 `raw` 供核对，尚不发布可信公司行为或用于回测。

**Q：自动同步会重建因子或前复权吗？**
A：会更新生产闭环内的前复权价格和日级价量因子，但只有当最新交易日的股票横截面覆盖率达到 95%、QFQ/curated 构建成功且核心因子通过发布闸门时才原子发布。自动链不触发研究回测、组合、风控或交易推演；历史回填和非生产因子仍需显式运行 `backend/scripts/` 下对应脚本。

**Q：端口被占用 / DuckDB 写冲突？**
A：DuckDB 服务应使用单个 worker；同一时间不要并行运行其他写库调度脚本。正常关闭会等待进行中的同步完成，避免中断写库。

**Q：估值初筛名单为空是什么意思？**
A：名单依赖行业与 ST 状态等人工核实项，未核实或覆盖不足时会返回空/缺口，页面会给出逐股缺失原因；不要把空名单或缺失利润理解为零收益。

**Q：本地全量 pytest 被终止？**
A：先在 `backend/` 中用 `uv run --extra dev pytest tests/<目标文件>.py -q` 定位问题；局部通过不能代替完整检查。统一入口的 `--scope backend` 可单独运行后端检查。

**Q：当前如何检查 schema 初始化？**
A：数据库初始化入口位于 `backend/app/database/`，随 API 启动执行；统一检查入口运行完整 pytest。仓库当前没有 Alembic 迁移目录，既有 schema 升级由显式兼容桥处理，不能将初始化视为完整版本化迁移或回退能力。

## 7. 已知限制

- **单机单用户**：定位为个人平台，未做多租户、权限与并发写入设计。
- **不连接实盘**：本项目只用于选股研究与持仓管理，不接入券商、不做自动化交易、不发送真实订单；真实账户只读同步、券商回报对账与受控交易均不在项目范围内。所有写操作只落在本地模拟推演账户。
- **模拟执行采用确定性 full-fill paper 模型**：使用固定 `a_share_cost_v1` 费率与 2 bps 滑点，并检查执行日可信行情的涨跌停/停牌等约束；不模拟市场冲击、撮合队列或部分成交，也不还原逐日历史真实费率。
- **价值初筛不构成历史时点认证**：东方财富接口不提供可证明的供应商 as-of 时点，报告固定 `pit_verified=false`，不能用于历史全市场回测收益或自动交易。
- **滚动回测尚非完整自动研究验证**：支持专用滚动入口逐测试窗执行，并可用 `selection_policy=training_ic_v1` 在训练窗自动选择因子与方向，保存不可变训练产物并绑定运行；合同仍为 `training_artifact_verified=false`。参数敏感性与 champion/challenger 晋级尚未形成自动闭环，不能据此宣称完整策略验证或全自动 walk-forward。重叠测试窗拒绝拼接，跨窗不结转持仓，拼接曲线仅为研究统计量。
- **公司行为跨区间在事件会计落地前会被明确拒绝**；遇到公司行为、历史 ST/状态未知或缺失可信行情/涨跌停数据时，价格价值回测会拒绝运行。
- **温层已接入指定生产链路**：研究默认走温层读取；`curated/prices_qfq` 按开关接入 `daily_loop` 暂存构建、因子认证与 release 发布。其余 curated 数据集未因此自动接入；代码默认与 `.env.example` 的开关值不同，以运行配置为准。
- **模拟盘回撤口径**为“自监控开始以来”，不是完整历史回撤。
- **风格代理因子为显式研究假设**（size=ln 市值、value=-PB、momentum/volatility/liquidity=factor_value 的横截面 z-score），基准成分股缺失因子时会主动回退为组合暴露。
- **数据源不稳定**：单一数据源可能缺新 ETF、指数或特殊品种，需依赖补充源与人工修复闭环。
- **全量测试可能受机器资源限制**，排查方式见上方 FAQ；检查入口为 `scripts/ci_check.py`。

---

## 附：目录结构（当前仓库真实结构）

```text
backend/
├── app/            # FastAPI 应用代码（api / services / repositories / models / database / scheduler / factors / strategies / backtest / portfolio / risk / lake）
├── data/           # 本地数据与数据湖文件
├── scripts/        # 导入、因子、维护、调度入口
├── sql/            # SQL 资源
├── tests/          # 后端测试
├── pyproject.toml  # Python 项目配置（uv 管理）
└── uv.lock

frontend/
├── src/            # Vue3 前端工作台（pages / components / api 封装 / router）
├── package.json
├── vite.config.ts
└── .nvmrc          # Node 版本锁定

libs/
└── hithink_sdk/    # 独立 HiThink SDK / toolkit 包

aiDoc/              # 项目文档与正式设计记录（文档唯一真源入口为 AGENT.MD）
data/               # DuckDB 单库文件
scripts/
└── ci-check.sh     # 本地 CI 检查脚本
.github/workflows/
└── ci.yml
```

## 附：文档入口

- `AGENT.MD` —— 本仓库 AI 协作规则唯一真源
- `AGENTS.md` —— 兼容适配层
- `aiDoc/README.md` —— 文档导航入口
- `aiDoc/plans/roadmap.md` —— 路线图与阶段计划
- `aiDoc/relations/trusted-daily-data-center.md` —— 可信数据中心架构
- `aiDoc/data/daily-bar-contract.md` —— 日线字段契约
- `aiDoc/modules/schema-plan.md` —— 数据库 schema 规划
