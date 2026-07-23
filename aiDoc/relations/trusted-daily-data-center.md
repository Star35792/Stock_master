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
- 实盘交易闭环。
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

仍需谨慎推进：

- QMT/MiniQMT 或其他券商真实账户只读同步。
- 任何实盘写操作。
- 分钟线、Tick 和高频交易。

## 10. 存储与工具边界

存储已从 PostgreSQL 单库切换到 DuckDB 分层湖（clean cut，不搬运历史分析数据）。当前实际形态：

- 后端 DuckDB 单库（`data/stock_master.duckdb`）经 SQLAlchemy ORM 承载 `raw -> market -> factor -> strategy -> portfolio/risk -> trade` 的可信链路与控制面（`system.job_log` 等）。`daily_loop` 编排器实际读写的是这一路径。可信性仍由数据契约、质量校验和血缘语义定义，不因换库而放宽。
- `backend/app/lake` 是 Parquet/DuckDB 温层工具（`curated/prices_qfq`、`fundamentals_pit`、`factor` release），当前由离线脚本（`scripts/lake/`、`scripts/maintenance/build_curated_layer.py`）驱动，尚未接入 `daily_loop`。`factor_reads_curated` 关闭时因子仍走 ORM 路径；置真且 `adjust_type='qfq'` 时才注入 `CuratedDailyBarReader`。
- `libs/hithink_sdk/marketdb` 是独立 DuckDB SDK/toolkit，适合 notebook、离线研究和 HiThink dump 使用，不直接等同于后端 `market` 可信层。

目标分层架构（MinIO 冷存、Parquet release + manifest 原子切换、Redis 热层、PostgreSQL 控制面/DuckDB 温层分离）见 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`；其中 MinIO 客户端、Parquet release 发布/校验、Redis 缓存目前仍是 config-only 桩，属后续切片。

任何会进入策略、回测、组合和模拟盘的生产数据，仍应以经过质量校验和血缘追踪的 `market` / `factor` 层为准。
