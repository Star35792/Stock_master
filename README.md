# Stock Master

> 说明：本文档为按当前仓库扫描生成的版本。安装命令、运行命令、访问地址、部署路径等**尚未最终确定**，相关位置统一用 `【待补】` 占位，请自行填写后再对外使用。文档中引用的目录、模块、脚本、配置项均为仓库中**当前真实存在**的内容，未做虚构。

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

<!-- 命令与路径待定，以下为占位；确认后请替换 -->

1. 准备 Python 环境（项目在 `backend/pyproject.toml` 中要求 `>=3.13,<3.14`）：
   ```bash
   # 【待补：后端环境准备命令】
   ```
2. 安装后端依赖（仓库使用 `uv`，并提供 `backend/uv.lock`）：
   ```bash
   # 【待补：后端依赖安装命令，可参考 uv sync --extra dev --extra data 等可选依赖组】
   ```
3. 准备环境变量文件（`backend/.env.example` 为模板，需复制为 `backend/.env`）：
   ```bash
   # 【待补：环境变量初始化命令】
   ```
4. 准备前端依赖（`frontend/package.json`，Node 版本锁定见 `frontend/.nvmrc`）：
   ```bash
   # 【待补：前端依赖安装命令】
   ```

可选依赖组（在 `backend/pyproject.toml` 中真实存在）：`data`、`backtest`、`viz`、`dev`、`all`。

## 3. 运行方式

### 后端服务

- 应用入口：`backend/app/main.py`（FastAPI 应用工厂 + lifespan，启动时初始化数据库并拉起后台 worker）。
- 启动时会启动三个后台任务组件：`AutoDataSyncWorker`、`PriceValueInputPrepWorker`、`PriceValueScreenWorker`（可在 `app.state` 中访问）。
- 数据库连接由 `backend/app/database/session.py` 的 `initialize_database()` 负责，首次启动创建表结构。
- 启动命令：
  ```bash
  # 【待补：后端启动命令】
  ```
- 服务地址：
  ```
  # 【待补：后端监听地址与端口】
  ```
- 健康检查接口（真实存在）：`GET /health`，路由文件 `backend/app/api/routes/health.py`。

### 前端工作台

- 开发服务器脚本：`frontend/package.json` 中的 `dev` / `build` / `preview`。
- 开发端口与后端代理在 `frontend/vite.config.ts` 中配置（`server.port` 与 `/api`、`/health` 代理目标，可用环境变量 `STOCK_MASTER_API_URL` 覆盖后端地址）。
  ```bash
  # 【待补：前端启动命令】
  ```
- 前端访问地址：
  ```
  # 【待补：前端访问地址】
  ```

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

- 仓库根目录存在 `scripts/ci-check.sh`（Bash，Git Bash / Linux / macOS 可用），流程为：后端 ruff → 后端核心测试 → 后端全量测试 → `alembic check`（非阻断）→ 前端 `npm run build`。
- GitHub Actions 配置：`.github/workflows/ci.yml`。
- 执行命令：
  ```bash
  # 【待补：本地/远程校验命令】
  ```

## 4. 使用示例

> 下列接口路径均来自 `backend/app/api/routes/` 的真实路由文件；主机地址、端口与完整请求体字段请按部署环境补齐后再作为示例使用。

### 健康检查

```bash
# 【待补：健康检查调用示例】
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
- `GET /api/v1/factors/batch-profiles/summary`

### 研究与回测

路由文件：`strategy_research.py`、`backtests.py`

- `POST /api/v1/strategy-research/rolling-windows`
- `POST /api/v1/strategy-research/rolling-backtests`
- `POST /api/v1/strategy-research/factor-selection/backtests`
- `POST /api/v1/strategy-research/factor-selection/backtests/batch`
- 价格价值研究：`POST /api/v1/strategy-research/price-value/evaluate`
- 价格价值假设回测：`POST /api/v1/strategy-research/price-value/backtests`、`GET /api/v1/strategy-research/price-value/backtests/{run_id}`
- 全市场相对估值初筛：`GET /api/v1/strategy-research/price-value/screen/latest`、`GET /api/v1/strategy-research/price-value/profit-status`
- 回测运行：`GET /api/v1/backtests/runs`、`GET /api/v1/backtests/runs/{run_id}`、`.../trades`、`.../equity`、`.../positions`、`.../performance`
- 基准与对比：`GET /api/v1/backtests/runs/{run_id}/benchmark`、`POST /api/v1/backtests/runs/compare`、`POST /api/v1/backtests/runs/drift`、`POST /api/v1/backtests/runs/validation-report`

### 组合、风控与模拟盘

路由文件：`portfolio.py`、`paper_accounts.py`、`approvals.py`

- 组合草案：`POST /api/v1/portfolio/plans`（需 `Idempotency-Key`）、`GET /api/v1/portfolio/plans/{plan_id}`、`GET /api/v1/portfolio/plans/{plan_id}/risk-readiness`
- 正式风控评估：`POST /api/v1/portfolio/plans/{plan_id}/risk-evaluations`（需 `Idempotency-Key`）
- Paper 账户：`GET /api/v1/paper-accounts`、`GET /api/v1/paper-accounts/{account_id}/ledger-audit`
- 净值快照：`POST /api/v1/paper-accounts/{account_id}/equity-snapshots`（需 `Idempotency-Key`）、`GET /api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}`
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

`/overview`、`/market`、`/factors`、`/price-value`、`/backtests`、`/portfolio`、`/paper`、`/quality`、`/operations`、`/alerts`、`/failures`（`/` 重定向到 `/overview`，未匹配路径同样重定向）。

页面实现在 `frontend/src/pages/`：`MarketPage.vue`、`FactorPage.vue`、`PriceValuePage.vue`、`PaperTradingPage.vue`、`WorkspaceRoutePage.vue` 及其 `components/` 子目录；API 封装在 `frontend/src/api.ts`、`priceValueApi.ts`、`priceValueBacktestApi.ts`、`priceValueScreenApi.ts`。

## 5. 关键配置说明

配置通过 `pydantic-settings` 加载，环境变量文件为 `backend/.env`（模板 `backend/.env.example`），定义在 `backend/app/config/settings.py`。

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `APP_NAME` | 应用名（FastAPI title） | `stock-master` |
| `APP_ENV` | 运行环境标识 | `dev` |
| `APP_DEBUG` | 调试开关 | `true` |
| `METADATA_DB_PATH` | DuckDB 单文件路径，派生 `database_url`（`duckdb:///...`） | `data/stock_master.duckdb` |
| `AKSHARE_TIMEOUT_SECONDS` | AkShare 超时 | `30` |
| `HITHINK_FINANCE_API_KEY` | 同花顺金融数据服务密钥（缺失时自动同步记录警告并跳过） | 空 |
| `HITHINK_FINANCE_BASE_URL` | 同花顺服务地址 | `https://fuyao.aicubes.cn` |
| `HITHINK_FINANCE_TIMEOUT_SECONDS` | 同花顺超时 | `30` |
| `HITHINK_DUMP_CACHE_DIR` | 原始包落地目录 | `data/hithink_dumps` |
| `AUTO_DATA_SYNC_ENABLED` | API 启动后的自动日线同步开关 | `true` |
| `AUTO_DATA_SYNC_INTERVAL_SECONDS` | 自动同步检查间隔（最小 60 秒） | `3600` |
| `DATA_LAKE_ROOT` | Parquet 温层根目录 | `data/warm` |
| `RAW_LANDING_ROOT` | 原始落地根目录 | `data/raw_landing` |
| `FACTOR_READS_CURATED` | 价格类因子是否改读 `curated/prices_qfq` Parquet（仅 qfq 生效） | `false` |
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
- 部署地址、域名、端口等：
  ```
  # 【待补：部署地址/域名/端口配置】
  ```

## 6. 常见问题

**Q：首次启动需要做什么？**
A：复制 `backend/.env.example` 为 `backend/.env` 并按需填写；应用启动时 `initialize_database()` 会创建表结构，无需手动建库。

**Q：没有配置 `HITHINK_FINANCE_API_KEY` 会怎样？**
A：自动同步任务记录警告并跳过，失败写入作业日志（作业名 `auto_data_sync`），下个周期重试；其余只读接口不受影响。

**Q：自动同步多久跑一次？**
A：默认每小时检查一次（`AUTO_DATA_SYNC_INTERVAL_SECONDS=3600`，最小 60 秒）。首次用供应商历史全量包建立基线，之后只扫描近期包补缺失或修订；停机超过近期包覆盖范围会自动回补历史。上海时间 18:00 前不发布当天日线。

**Q：自动同步会重建因子或前复权吗？**
A：不会。自动任务只更新未复权日线，不自动重建前复权、因子、回测或交易，相关动作需显式运行 `backend/scripts/` 下对应脚本。

**Q：端口被占用 / DuckDB 写冲突？**
A：DuckDB 服务应使用单个 worker；同一时间不要并行运行其他写库调度脚本。正常关闭会等待进行中的同步完成，避免中断写库。

**Q：估值初筛名单为空是什么意思？**
A：名单依赖行业与 ST 状态等人工核实项，未核实或覆盖不足时会返回空/缺口，页面会给出逐股缺失原因；不要把空名单或缺失利润理解为零收益。

**Q：本地全量 pytest 被终止？**
A：全量测试在资源密集场景下可能被系统 SIGTERM；`scripts/ci-check.sh` 中的核心测试分组（API、回测、组合、风控、模拟执行、策略模板）可独立稳定运行。

**Q：`alembic check` 失败要立刻修吗？**
A：`scripts/ci-check.sh` 中该步骤为非阻断（`|| true`），但仍应视为 schema 与模型不一致的信号，按项目规则用 Alembic 补迁移。

## 7. 已知限制

- **单机单用户**：定位为个人平台，未做多租户、权限与并发写入设计。
- **不连接实盘**：本项目只用于选股研究与持仓管理，不接入券商、不做自动化交易、不发送真实订单；真实账户只读同步、券商回报对账与受控交易均不在项目范围内。所有写操作只落在本地模拟推演账户。
- **模拟执行使用固定 full-fill paper 模型**，不模拟滑点、冲击成本与真实撮合。
- **价值初筛不构成历史时点认证**：东方财富接口不提供可证明的供应商 as-of 时点，报告固定 `pit_verified=false`，不能用于历史全市场回测收益或自动交易。
- **滚动回测不是全自动 walk-forward**：训练与选参仍由调用方完成，训练产物验证开关默认未验证；重叠窗口禁止直接复利。
- **公司行为跨区间在事件会计落地前会被明确拒绝**；遇到公司行为、历史 ST/状态未知或缺失可信行情/涨跌停数据时，价格价值回测会拒绝运行。
- **Parquet 温层尚未全链路接入**：`backend/app/lake` 已就绪但主要由离线脚本驱动，与 `daily_loop`、Parquet release 的接入是后续切片；`FACTOR_READS_CURATED` 默认关闭。
- **模拟盘回撤口径**为“自监控开始以来”，不是完整历史回撤。
- **风格代理因子为显式研究假设**（size=ln 市值、value=-PB、momentum/volatility/liquidity=factor_value 的横截面 z-score），基准成分股缺失因子时会主动回退为组合暴露。
- **数据源不稳定**：单一数据源可能缺新 ETF、指数或特殊品种，需依赖补充源与人工修复闭环。
- **本地全量测试在低配机器上可能因资源累积被终止**，CI 分组策略见 `scripts/ci-check.sh`。

---

## 附：目录结构（当前仓库真实结构）

```text
backend/
├── app/            # FastAPI 应用代码（api / services / repositories / models / database / scheduler / factors / strategies / backtest / portfolio / risk / lake）
├── alembic/        # 数据库迁移
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