# V1 可信日线数据中心架构

## 1. 背景

Stock Master 的第一阶段不把任何单一外部数据源视为可靠真源。免费接口、网页接口、第三方 SDK、券商本地数据和手工 CSV 都可能存在以下问题：

- 字段缺失或字段含义变化。
- 行情延迟、停更或历史回填不完整。
- 交易日历、停牌、除权除息口径不一致。
- 前复权、后复权、不复权混用。
- 成交量、成交额单位不一致。
- 同一证券同一交易日，多来源价格不同。

因此 V1 的目标不是“接入某个接口”，而是建立一个本地可信日线数据中心：所有来源先留痕，再清洗、校验、对账、修复，最终产出可供策略和回测使用的 `market` 层数据。

## 2. 设计目标

V1 需要满足：

1. **多源导入**：支持 CSV、API、AKShare、QMT/MiniQMT、手工补录等来源逐步接入。
2. **原始留痕**：外部数据先写入 `raw`，保留来源、批次、原始字段和导入上下文。
3. **标准化输出**：`market` 层使用统一字段契约，屏蔽来源差异。
4. **质量校验**：对完整性、交易日、OHLC、成交量、复权、多源冲突等问题进行检测。
5. **可追溯修复**：人工修复必须记录修复前后值、原因、处理人和关联来源。
6. **消费隔离**：因子、策略、回测、API 查询只能读取 `market` 层可信数据。

## 3. 当前边界

本文最初定义 V1 可信日线数据中心。当前代码已经在该底座上继续推进到因子、回测、组合、风控、模拟盘和运维工作台。V1 的非目标仍表示“可信日线数据中心本身不直接承担这些职责”，不表示仓库当前没有这些能力。

V1 数据中心本身暂不承担：

- 分钟线、Tick 数据。
- 因子平台的研究方法设计。
- 策略 DSL 或完整研究平台。
- 实盘交易闭环与自动化下单（永久不在项目范围）。
- 对所有历史财务数据的一次性完整建模。

这些能力在日线可信数据中心稳定后再逐步扩展。

## 4. 三层数据架构

```text
外部数据源
  │
  ├── CSV / Excel
  ├── API / 网页接口
  ├── AKShare
  ├── QMT / MiniQMT
  ├── 手工补录
  └── 付费源 / 交易所文件
          │
          ▼
raw 原始层
  - import_batch
  - raw daily bar
  - 原始 payload
  - 来源和批次
          │
          ▼
清洗、标准化、质量校验、对账、修复
          │
          ▼
market 可信层
  - security
  - trading_calendar
  - daily_bar
  - source_compare
  - manual_correction
          │
          ▼
factor / strategy / portfolio / risk / trade
          │
          ▼
运维概览 API / Vue3 工作台 / 查询 API
```

### 4.1 raw 层

`raw` 是事实留痕层，不代表可信。

职责：

- 保存每次导入批次。
- 保存来源返回或文件解析得到的原始数据。
- 保存 `raw_payload` 以便追溯源字段。
- 允许存在重复导入、冲突数据、异常数据。
- 不直接被策略和回测读取。

### 4.2 market 层

`market` 是可信消费层。

职责：

- 保存统一字段契约下的证券主数据、交易日历和日线数据。
- 每条可信日线应可追溯到一个或多个 `raw` 来源。
- 记录质量状态、质量评分、是否人工修复。
- 给因子、策略、回测和 API 查询提供稳定接口。

### 4.3 system 层

`system` 是任务、质量和审计层。

职责：

- 记录导入任务、清洗任务、校验任务、修复任务和因子计算任务（`system.job_log`）。
- 记录数据质量问题。
- 记录来源配置、任务状态、异常信息。
- `scheduler.daily_loop` 每次运行留下 running → success/failed/partial 记录，支撑调度排障。

## 5. 核心数据流

```text
DataSource Adapter
      │
      ▼
ImportService
      │  创建批次、读取来源、标准化来源元信息
      ▼
raw.import_batch + raw.daily_bar
      │
      ▼
NormalizeService
      │  统一 symbol、日期、单位、复权类型、字段名
      ▼
ValidationService
      │  完整性、OHLC、交易日历、成交量、涨跌幅等规则
      ▼
system.data_quality_issue
      │
      ▼
ReconciliationService
      │  多源对账、来源选择、冲突标记
      ▼
market.daily_bar
      │
      ▼
API / Factor / Strategy / Backtest
```

## 6. 关键约束

### 6.1 数据源不允许直接写 market

任何外部来源必须先进入 `raw`，再经过清洗和校验后进入 `market`。

禁止：

```text
AKShare / CSV / QMT  →  market.daily_bar
```

允许：

```text
AKShare / CSV / QMT  →  raw.daily_bar  →  校验/对账  →  market.daily_bar
```

### 6.2 策略和回测不读取 raw

`raw` 中可能包含错误、重复、冲突和未标准化数据。策略和回测只能读取：

- `market.daily_bar`
- 后续的 `factor.*`
- 后续的 `strategy.*`

### 6.3 market 数据必须可追溯

`market.daily_bar` 需要记录：

- 最终采用的数据源。
- 参与对账的数据源数量。
- 质量评分和质量状态。
- 选中的 `raw` 记录 ID 或关联关系。
- 是否人工修复。

### 6.4 人工修复必须审计

人工修复不能静默覆盖。每次修复必须记录：

- 修复字段。
- 修复前值。
- 修复后值。
- 修复原因。
- 修复人。
- 修复时间。
- 关联问题或来源。

## 7. 模块边界

建议代码分层如下：

```text
backend/app/
├── api/            # HTTP 接口，只调用 service
├── data_sources/   # CSV/API/QMT/AKShare 等来源适配器
├── services/       # 导入、清洗、校验、对账、修复流程
├── repositories/   # 数据库读写、upsert、查询
├── models/         # SQLAlchemy ORM 模型
├── quality/        # 数据质量规则、评分、校验器
├── scheduler/      # 定时任务
└── database/       # Base、Session、schema、连接
```

依赖方向：

```text
api / scripts / scheduler
        ↓
services
        ↓
repositories
        ↓
models / database
```

`data_sources` 只负责获取和初步解析外部数据，不负责写 `market`。

## 8. 示例闭环：CSV 与 API 冲突

场景：同一只股票同一天，CSV 收盘价为 `10.25`，API 收盘价为 `10.80`。

处理流程：

1. CSV 导入生成一个 `raw.import_batch`，数据写入 `raw.daily_bar`。
2. API 导入生成另一个 `raw.import_batch`，数据也写入 `raw.daily_bar`。
3. 标准化后发现两条记录的 `symbol + trade_date + adjust_type` 相同。
4. 对账服务计算价格差异并写入 `market.daily_bar_source_compare`。
5. 质量服务生成 `SOURCE_CONFLICT` 问题，状态为 `open`。
6. 若差异超过阈值，不允许自动覆盖 `market.daily_bar`。
7. 人工选择可信来源或手工输入修正值，写入 `market.manual_correction`。
8. `market.daily_bar` 更新为 `manual_fixed` 或 `trusted` 状态。
9. 回测只读取修复后的 `market.daily_bar`，不直接读取 CSV 或 API 原始记录。

## 9. 后续演进

V1 稳定后已经扩展出的能力：

- 复权因子和除权除息事件。
- ETF、指数、行业指数统一建模。
- 因子工厂。
- 策略引擎。
- 回测系统。
- 组合和风控。
- Vue3 可视化平台。

明确不在项目范围内（永久约束）：

- 券商真实账户接入与任何实盘写操作：本项目不连接券商，不做实盘自动化交易；实际下单由人工在外部券商终端完成。
- QMT/MiniQMT 或其他券商终端仅可作为行情/基础数据的**采集来源**，其任何账户、持仓、委托与成交写操作一律不接入本系统。
- 分钟线、Tick 和高频交易。

## 10. 存储与工具边界

存储已从 PostgreSQL 单库切换到 DuckDB 分层湖（clean cut，不搬运历史分析数据）。当前实际形态：

- 后端 DuckDB 单库（`data/stock_master.duckdb`）经 SQLAlchemy ORM 承载 `raw -> market -> factor -> strategy -> portfolio/risk -> trade` 的可信链路与控制面（`system.job_log` 等）。`daily_loop` 编排器实际读写的是这一路径。可信性仍由数据契约、质量校验和血缘语义定义，不因换库而放宽。
- `backend/app/lake` 是 Parquet/DuckDB 温层工具（`curated/prices_qfq`、`fundamentals_pit`、`factor` release），当前由离线脚本（`scripts/lake/`、`scripts/maintenance/build_curated_layer.py`）驱动，尚未接入 `daily_loop`。`factor_reads_curated` 关闭时因子仍走 ORM 路径；置真且 `adjust_type='qfq'` 时才注入 `CuratedDailyBarReader`。
- `libs/hithink_sdk/marketdb` 是独立 DuckDB SDK/toolkit，适合 notebook、离线研究和 HiThink dump 使用，不直接等同于后端 `market` 可信层。

分层存储架构（DuckDB 单库承载控制面+事务+温层查询、本地 Parquet 温层、本地 FS 冷存；不引入 PostgreSQL/SQLite/MinIO/Redis）见 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`。`curated/prices_qfq` 已接入 `daily_loop`（`factor_reads_curated` 开关），`fundamentals_pit`、factor release 与完整 Parquet release 发布/校验仍属后续切片。

### 10.1 研究读路径（2026-10-22 新增）

回测与因子研究的**读**路径已经可以直接读温层 Parquet，**写路径仍然只走 ORM**，控制面不因研究读而变大。

- `app/repositories/research_read.py` 的 `WarmResearchReadRepository` 是 ORM 仓库的只读孪生实现，暴露 `list_daily_bars` / `list_factor_values` / `load_execution_calendar` / `coverage` / `total_trading_days` / `max_exported_date` / `tradability_coverage`。日线读取时会用 `app/market/price_limit.py` 补齐 `limit_up` / `limit_down`（控制面这两列全为 NULL）。
- `app/services/backtest.py` 与 `app/services/strategy_research.py` 通过 `_research_read()` / `_factor_read()` 上下文管理器切换数据源，`research_read_source=warm|control`。**单元测试固定用 `control`**（`tests/conftest.py` 设置 `RESEARCH_READ_SOURCE=control`），否则临时测试库无法自己 ATTACH 自己。
- 两道 fail-closed 闸门：`research_min_trading_days`（默认 200，release 被截断时直接失败）与「窗口末日不得超过 `max_exported_date`」。不要为了让回测跑起来而调低它们。
- 温层连接默认 `attach_control=False`，即纯 Parquet 连接，不 ATTACH 控制面文件——DuckDB 不允许同一文件被两个句柄打开（其中一个为读写）时报 `Unique file handle conflict`。`market.corporate_action` 与 `market.trading_calendar` 因此仍走 ORM（这两张表未被瘦身，持有完整历史）。
- 子 release 只发布变更表，其余表通过 `WarmLayerStore.base_release_tag` 从父 release 继承，避免每次复制约 700 MB Parquet。

涨跌停价格：`limit_up` / `limit_down` 不再依赖供应商字段，改由 `app/market/price_limit.py` 按板块从当日 `pre_close` 推导（主板 ±10%、创业板/科创板 ±20%、北交所 ±30%、B 股 ±10%，主板 ST 降为 ±5%，ROUND_HALF_UP 到分）。锚点必须是**当日自身的 `pre_close`**，不能用前一行的 `close`——除权除息日两者不相等。指数（`.SH` + `000` 前缀，如 `000300.SH`）不套用涨跌幅带。

任何会进入策略、回测、组合和持仓推演的生产数据，仍应以经过质量校验和血缘追踪的 `market` / `factor` 层为准。

API 启动后的日线维护由 `scheduler/auto_data_sync.py` 的后台线程执行，调用
`services/auto_data_sync.py` 编排参考数据刷新、HiThink dump 幂等导入和可信日线构建。
首次使用历史包建立处理基线，之后按成功任务水位与近期包实际窗口判断增量或历史回补，
通过 `JobLog` 留痕；任务失败不推进水位。原始记录已经存在时仍可恢复缺失 market，
已人工修复的数据不被自动覆盖。自动链仅处理未复权日线，不隐式扩展到因子、研究或交易。
生命周期任务不阻塞 API 启动，退出时等待当前任务结束；单 worker 部署，避免并行写库脚本。

价值研究另设独立的晚间输入准备链。它先要求同日 `auto_data_sync` 成功且 SH/SZ/BJ 日历齐备，再将东方财富估值与业绩报表 ROE 的每页原始响应写入 `raw.vendor_snapshot`，批次记录在 `raw.import_batch`。目标交易日取上海本地当天且仅在 21:00 后采集，分页及发布前再次检查日期，跨午夜不发布；当前 clist 接口不可用时，只能从供应商标注**同日** `TRADE_DATE` 的估值分析分页退回，仍须与可信未复权收盘价相差至多 0.01 元。ROE 使用已公告的加权平均净资产收益率，季度初空报告期向前查找，保留报告期、公告日、披露时刻与原始页哈希；没有完整首次可得和修订版本证明。分页总数、去重、沪深北各自的证券及日线覆盖、供应商市场码、价格、有效 PE/PB 与 ROE 覆盖验收通过后，才写入带 `_research_batch_id` 的研究观察行；通用估值因子、市值读取和晚间 ROE 因子写入均与之隔离。失败批次保留供审计。旧东方财富估值直写入口已禁用。午夜报告要求同日成功准备任务和绑定的 raw 批次，缺输入时阻断，不使用旧日估值；该报告不获得历史 PIT 或自动执行资格。证券参考数据本身也先入 raw，HiThink 上市日低覆盖时有限重取，未知 ST 仍逐股阻断。

指定历史交易日的补采另走东方财富估值分析日期接口，独立批次只写 raw 和任务日志，逐页保存原始 JSON、响应哈希与真实 UTC 观察时间。供应商 `TRADE_DATE` 是其数据标签，不证明该版本在当日已可得。当前通用估值因子按交易日期消费未标研究隔离的 `market.valuation_snapshot`，因此历史补采在可得时间和修订血缘落地前不得发布到 market、factor、筛选或回测。
