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

回测详情、利润状态、基准比较及回测失败重跑计划的产物读取使用独立研究库；研究忙时沿用 409。组合页“复制回测目标权重”使用期末总权益作为零现仓预算，不复制历史实际数量。通用日频回测对持仓证券开市日缺可信行情返回明确错误，不静默填价；历史运行保持不变。

- 检查命令唯一维护在 `scripts/ci_check.py`，本地和 `.github/workflows/ci.yml` 共用。
- 后端对 `app/`、`tests/`、`backend/scripts/` 和统一检查脚本执行 Ruff，再运行一次完整 pytest，自动收集 `tests/` 下的测试。标记为 `production_data` 的只读生产数据审计默认跳过，避免 CI 或本地单测隐式读取 `data/stock_master.duckdb`；显式设置 `RUN_PRODUCTION_DATA_AUDIT=1` 后单独运行，数据库路径可用 `PRODUCTION_CONTROL_PLANE_PATH` 指定，此时路径不存在或数据库无法只读打开会直接失败而不是静默跳过。前端执行 package.json 中已配置的 `test` 脚本（未配置则跳过），再执行类型检查/构建；任一步失败立即返回非零状态。
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
- `GET /api/v1/adjustment-factors`（默认 `scope=production`，固定返回可信生产复权因子 `hithink_finance_dump@v1`；审计历史来源、版本或非可信状态须显式使用 `scope=all`）

### 因子

路由文件：`backend/app/api/routes/factors.py`

- `GET /api/v1/factors/definitions`
- `GET /api/v1/factors/runs`
- `GET /api/v1/factors/values`
- `GET /api/v1/factors/profiles`
- `POST /api/v1/factors/batch-profiles`
- `GET /api/v1/factors/batch-profiles/summary`（返回评估窗口统一的代码版本、各 horizon 非空观测数，以及 `evidence_eligibility_count`、`evidence_identity_count`、`evidence_identity_valid`、输入 release tag 和数据库实际日期 hash；混合或截取窗口会 fail-closed）
- `POST /api/v1/factors/pool-classifications`（因子池分级：端点、PIT、生命周期、证据资格、代码版本和输入身份一致性闸门；旧文本、混合 release/QFQ、部分重算、缺日及占位版本不得进入核心池）

### 研究与回测

路由文件：`strategy_research.py`、`backtests.py`

- `POST /api/v1/strategy-research/rolling-windows`
- `POST /api/v1/strategy-research/rolling-backtests`（可选 `selection_policy: "training_ic_v1"` 启用训练窗自动选参——逐窗按预注册 RankIC 门槛从候选池选因子/方向并写入 `training_artifact`，测试窗纯样本外执行；见 `aiDoc/plans/20261024-1522-training-window-selection-plan.md`。可选 `quantile_backtest` 在每个测试窗内做分层回测。返回 `RollingBacktestResult`：各窗 `windows` + 跨窗拼接的 `combined` 组合净收益曲线（重叠窗 fail-closed 拒绝拼接、各窗仍独立可用）+ 非可交易声明）
- `POST /api/v1/strategy-research/factor-selection/backtests`（支持 `rebalance_rule: daily|weekly|monthly|every_n_trading_days` + `rebalance_interval`，以及可选 `portfolio_constraints`：单标的上限/行业上限/现金留存率/证券类型预算，经 PortfolioBuilder 与 paper 链路同源。可选 `quantile_backtest`：`split_factor_name/version` + `n_layers`(2–20) + `max_assets_per_layer` 按 split 因子在决策日横截面分桶；当前仅接受 `weighting_mode=equal_weight_fully_invested`，其他模式 fail-fast，逐桶等权多头跑净收益曲线与 top–bottom 统计收益差；换层以 `explicit_zero_exit_v2` 显式退出旧成员，分层分支独立读取并双快照校验全量证券行情，结果内嵌主运行 `parameters.quantile_backtest` / `quantile_data_snapshot` 并随响应返回 `quantile`）
- `POST /api/v1/strategy-research/factor-selection/backtests/batch`（单项成功即提交独立研究库，失败项回滚后继续；`code_version=auto` 由服务端代码摘要解析，`data_version=auto` 在运行成功后由实际消费的行情、因子、基准摘要解析。工作台默认使用 `a_share_cost_v2`，股票印花税可按成交日期分段，ETF 不套用股票税；旧 `v1` 合同保留）
- `POST /api/v1/strategy-research/factor-selection/backtests/preflight`（与单次因子选股回测使用同一请求体，只读预演因子、调仓日、可信未复权行情、公司行为及日频引擎；返回检查时间、输入 SHA-256、`runnable_now`、`evidence_qualified` 与逐项原因。预检不创建研究运行，正式运行仍重新校验；当前不覆盖 rolling 与分层附加结果）
- 价格价值研究：`POST /api/v1/strategy-research/price-value/evaluate`
- 价格价值假设回测：`POST /api/v1/strategy-research/price-value/backtests`、`GET /api/v1/strategy-research/price-value/backtests/{run_id}`
- 全市场相对估值初筛：`GET /api/v1/strategy-research/price-value/screen/latest`、`GET /api/v1/strategy-research/price-value/screen/tracking`、`GET /api/v1/strategy-research/price-value/profit-status`
- 内置策略定义目录：`GET /api/v1/strategy-research/definitions`（列出已注册的内置策略及其锁定参数）、`POST /api/v1/strategy-research/definitions/register-all`（幂等注册）
- 内置价值投资选股：`POST /api/v1/strategy-research/value-investing/screen`（只读；按登记在案的锁定安全边际 30%、3 年年化 ROE 质地闸门、价值陷阱闸门（4 年年报窗口的营收/净利连跌年数、负债率 3 年变化）与成长路径（营收/净利 3 年 CAGR），跑准入 → 质量 → 价值 → 安全边际四层漏斗，输出达标池、优先研究名单、企业质地体检、价值陷阱闸门命中统计与「仅价格闸门」对照，不产生权重或订单）
  - 闸门语义：**未知即阻断、已知不合格即排除**——价值陷阱命中（连跌 ≥ 2 年或负债率上行 > 10pp）判 `excluded`，趋势窗口不足 4 期判 `blocked`。成长路径只替代 ROE 质地闸门，**绝不替代价格闸门**；**陷阱闸门优先于成长路径**（营收涨而利润跌、杠杆上升是带好标题的陷阱，不是成长故事）。**DCF 不进闸门**（对现金流与折现率假设敏感），估值锚（历史倍数保守锚，相对自身历史倍数、不是内在价值）用盈利锚与净资产锚取较小者，绝对估值由只读 FCFE 情景研究承担。
  - 显式 `current_research_catchup` 模式可补采最新完整交易日的研究观察，报告可选返回 `observation_mode`、`price_as_of`、`financial_as_of`、`analysis_at`、`observed_at`。页面分别展示行情日期、财务日期、真实分析与采集时刻，并标明“当前补采研究观察，非当晚历史名单”；财务可得截止为实际分析时刻，固定未认证历史 PIT，通用因子与历史回测保持隔离。
  - 初筛 v7 在读取时返回 `freshness`（上海三所日历核验）和 `candidate_eligible`；不改写当前报告及其哈希。只保留最新全量报告与冻结 cohort，旧任务保留摘要和快照哈希，旧报告无法精确展开。逐股 `research_diagnostics` 提供 20 日复权走势、三年利润/现金流和经营现金流 TTM、新增净利润 TTM（报表净利润，非扣非／归母）、来源、缺口及输入哈希；`research_candidate_count` 保留原走势与现金流条件池数量，不改成精选数量。
  - `selection.version=value_research_shortlist_v2` 要求净利润 TTM 严格为正；缺失或冲突待核查、非正排除。返回冻结的 `parameters`、`qualified_count`（全部达标池）、`shortlist_count`（优先研究数量）、按研究顺序排列的 `ordered_symbols`、`reason_counts` 及 `ranking_description`。逐股 `shortlist` 返回 `status/qualified/selected/rank/reason_codes/metrics`；容量递延状态为 `qualified_deferred`，不是淘汰。默认页面只显示后台优先研究名单，最多 10 只、每行业最多 2 只，可显式切换达标池、原条件池或基础准入记录；原始行仍须主动打开。个人 PE/PB/ROE 过滤只缩小当前视图，不重新排序、不补位、不解除后台门槛。旧版本或缺精选合同的报告不能产生新版候选。
  - `selection.rule_impact` 在同一份 v7 输入上构造“仅移除净利润 TTM 门槛”的反事实，分别统计 TTM 非正、缺失/冲突、混合原因及名单变化；它用于归因新增门槛，不把不同观察时刻的旧报告差异归因给单一规则。
  - `screen/tracking` 使用不可变 cohort sidecar 读取冻结的优先名单、达标池与原条件池，按真实形成日后的首个共同交易日收盘开始，固定观察 5/20/60 个交易日。成员等权冻结、不补入、不再平衡；可信前复权行情缺失的成员仍保留在分母并把池标为不完整。返回毛观察收益、固定比例成本后结果、相对原条件池差异、覆盖和输入哈希；补采 cohort 单独标识。该接口是动态只读前向观察，不改写原报告或哈希，不是历史回测、真实组合收益或策略认证。
  - 精选另要求供应商 PE≤20、PB≤2、上市至少 730 天、20 日平均成交额至少 5000 万元、最近三年逐年盈利、较 MA20 偏离不超过 8%，主期及已核验更新期 ROE 为正；周期行业与异常 ROE 需复核。排序和全部规则见价值策略文档。当前名单始终须通过鲜度和基础准入；这些规则只安排研究工作量，未完成绝对估值或样本外收益验证。
- 回测运行：`GET /api/v1/backtests/runs`、`GET /api/v1/backtests/runs/{run_id}`、`.../trades`、`.../equity`、`.../positions`、`.../performance`（strategy 产物存独立 `data/research.duckdb`，见 `aiDoc/relations/20261024-1427-research-database-split.md`）
- 基准与对比：`GET /api/v1/backtests/runs/{run_id}/benchmark`（基准可信行情须与策略净值日期逐日对齐；新运行使用经复核的运行时基准快照，返回 `input_source=run_snapshot`；旧运行返回 `current_market_unversioned`，其结果可能随可信行情修订变化。缺失返回 422）、`POST /api/v1/backtests/runs/compare`、`POST /api/v1/backtests/runs/drift`、`POST /api/v1/backtests/runs/validation-report`
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
A：仓库当前没有 Alembic 迁移目录；完整 pytest 包含 `tests/test_schema_smoke.py`，验证临时三文件布局和重复初始化。既有 schema 升级由显式兼容桥处理；该检查不等于已经具备完整版本化迁移或回退能力。

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
