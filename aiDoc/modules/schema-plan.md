# 数据库 Schema 规划

本文记录 V1 数据中心的初始表契约及后续已落地表域。当前模型与各库初始化代码是实现证据：主库 `app/database/session.py`、研究库 `research_session.py`、账本 `positions_session.py`。仓库没有 Alembic 迁移目录；主库以显式兼容桥维护既有 schema，尚无完整版本化迁移与回退体系。

## 1. 设计原则

2026-11-19 新增独立 SQLite `data/agent.db`（`AGENT_DB_PATH`），初始化入口 `app/database/agent_session.py::initialize_agent_database`，独立 `AgentBase`，不注册到 DuckDB metadata。两表：`agent_session(id, revision, state JSON, updated_at)` 与 `agent_memory(id, kind, content, source_session_id, revision, updated_at)`。修订号更新采用条件 UPDATE RETURNING；使用 WAL/FULL、短事务与5秒 busy timeout。会话最多64个、快照256KiB，显式记忆最多32条、每条400字符，详细用途/恢复边界见[整体计划](../plans/20261119-1049-agent-overall-plan-and-sqlite.md)。现有三库和温层数据不迁移；计划与统一权限还未交付。

运维修复（2026-10-28）：`scripts/maintenance/restore_financial_unique_indexes.py` 仅恢复ORM已声明的 `fundamental.financial_statement.uq_financial_statement_key` 与 `fundamental.financial_indicator.uq_financial_indicator_key`，以修复旧库缺唯一索引导致的财务upsert失败；不新增表/字段或改变唯一键语义。应用释放数据库后，脚本持共享写锁、检查重复和空键、事务创建并后验核对，保存独立JSON审计；有重复/空键即拒绝，不自动删行或去重。全库其他缺失约束只审计不扩展修复。

1. **分层存储**：原始数据、可信数据、系统审计分 schema 存放。
2. **先留痕再消费**：外部数据先进 `raw`，不能直接写入 `market`。
3. **字段标准化**：`market` 层字段必须符合 `aiDoc/data/daily-bar-contract.md`。
4. **质量可追踪**：导入、校验、冲突、修复都必须有记录。
5. **首批克制**：V1 只做日线数据中心；后续 schema 已按路线图继续扩展，但仍保持消费方向清晰。

## 2. Schema 划分

首批核心 schema：

```text
raw       原始导入层
market    可信市场数据层
metadata  任务、质量、审计层（业务模块名 system）
```

主库创建的 schema 名称定义在 `app/database/session.py::SCHEMAS`，不在 `base.py`。当前清单如下；创建 `strategy` 命名空间不表示主库持有研究产物表：

```python
SCHEMAS = (
    "raw",
    "market",
    "fundamental",
    "factor",
    "strategy",
    "portfolio",
    "risk",
    "trade",
    "lake",
    "metadata",
)
```

`system` 为历史文档中的业务域名，相关 ORM 表物理上位于 `metadata`；下文物理表清单使用 `metadata.*`。研究库由 `research_session.py` 创建 `strategy` schema，模型挂 `ResearchBase`；SQLite 账本独立初始化，不属于主库 schema。

## 3. 首批表清单

建议 V1 第一批表：

```text
raw.import_batch
raw.daily_bar

market.security
market.trading_calendar
market.daily_bar
market.daily_bar_source_compare
market.manual_correction

metadata.job_log
metadata.data_quality_issue
metadata.data_source
```

其中最小可运行闭环至少需要：

```text
raw.import_batch
raw.daily_bar
market.security
market.daily_bar
metadata.data_quality_issue
metadata.job_log
```

## 4. raw.import_batch

### 作用

记录每一次 CSV、API、QMT、手工补录等导入任务。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `batch_no` | `varchar(64)` | 是 | 批次号，唯一 |
| `source_name` | `varchar(50)` | 是 | 来源名称 |
| `source_type` | `varchar(20)` | 是 | `csv` / `api` / `akshare` / `qmt` / `manual` / `vendor` |
| `dataset` | `varchar(50)` | 是 | 数据集，例如 `daily_bar` |
| `file_path` | `text` | 否 | 文件路径 |
| `request_params` | `jsonb` | 否 | API 请求参数或导入参数 |
| `started_at` | `timestamptz` | 是 | 开始时间 |
| `finished_at` | `timestamptz` | 否 | 结束时间 |
| `status` | `varchar(20)` | 是 | `running` / `success` / `failed` / `partial` |
| `row_count` | `integer` | 是 | 原始行数，默认 0 |
| `accepted_count` | `integer` | 是 | 接收行数，默认 0 |
| `rejected_count` | `integer` | 是 | 拒绝行数，默认 0 |
| `error_message` | `text` | 否 | 错误信息 |
| `created_at` | `timestamptz` | 是 | 创建时间 |

### 约束与索引

```sql
unique(batch_no)
index(source_name, dataset)
index(status)
index(started_at)
```

## 5. raw.daily_bar

### 作用

保存所有来源的原始日线记录。`raw` 层允许重复、冲突和异常，但必须可追溯。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `batch_id` | `bigint` | 是 | `raw.import_batch.id` |
| `source_name` | `varchar(50)` | 是 | 来源名称 |
| `source_type` | `varchar(20)` | 是 | 来源类型 |
| `source_priority` | `integer` | 是 | 默认来源优先级 |
| `symbol` | `varchar(20)` | 是 | 标准证券代码 |
| `raw_symbol` | `varchar(30)` | 否 | 来源原始代码 |
| `security_type` | `varchar(20)` | 是 | 证券类型 |
| `trade_date` | `date` | 是 | 交易日期 |
| `adjust_type` | `varchar(10)` | 是 | 复权类型 |
| `open` | `numeric(18,4)` | 否 | 开盘价 |
| `high` | `numeric(18,4)` | 否 | 最高价 |
| `low` | `numeric(18,4)` | 否 | 最低价 |
| `close` | `numeric(18,4)` | 否 | 收盘价 |
| `pre_close` | `numeric(18,4)` | 否 | 前收盘价 |
| `change_amount` | `numeric(18,4)` | 否 | 涨跌额 |
| `pct_change` | `numeric(12,6)` | 否 | 涨跌幅 |
| `volume` | `numeric(24,4)` | 否 | 成交量 |
| `amount` | `numeric(24,4)` | 否 | 成交额 |
| `turnover_rate` | `numeric(12,6)` | 否 | 换手率 |
| `amplitude` | `numeric(12,6)` | 否 | 振幅 |
| `raw_payload` | `jsonb` | 否 | 来源原始字段 |
| `row_hash` | `varchar(64)` | 否 | 行内容 hash |
| `imported_at` | `timestamptz` | 是 | 导入时间 |

### 约束与索引

```sql
foreign key(batch_id) references raw.import_batch(id)
index(symbol, trade_date)
index(source_name, trade_date)
index(batch_id)
index(row_hash)
```

可选去重约束：

```sql
unique(source_name, symbol, trade_date, adjust_type, row_hash)
```

是否启用该唯一约束由实现阶段根据“是否允许完全重复 raw 留痕”决定。

## 6. market.security

### 作用

统一证券主数据，避免日线表重复存放名称、行业、上市状态等信息。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `symbol` | `varchar(20)` | 是 | 标准证券代码 |
| `raw_symbol` | `varchar(30)` | 否 | 原始代码 |
| `name` | `varchar(100)` | 是 | 证券名称 |
| `security_type` | `varchar(20)` | 是 | `stock` / `etf` / `index` |
| `exchange` | `varchar(10)` | 是 | `SH` / `SZ` / `BJ` |
| `currency` | `varchar(10)` | 是 | 默认 `CNY` |
| `list_date` | `date` | 否 | 上市日期 |
| `delist_date` | `date` | 否 | 退市日期 |
| `is_active` | `boolean` | 是 | 是否仍在交易 |
| `industry` | `varchar(100)` | 否 | 行业 |
| `area` | `varchar(100)` | 否 | 地区 |
| `board` | `varchar(50)` | 否 | 主板/创业板/科创板/北交所等 |
| `source_name` | `varchar(50)` | 否 | 主要来源 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

### 约束与索引

```sql
unique(symbol)
index(security_type)
index(exchange)
```

证券名称搜索使用包含匹配；约 5,000 只证券的主数据表无需单列名称索引。旧库的 DuckDB `ix_security_name` 在更新名称时可触发索引删除错误并使连接失效，启动兼容升级会幂等移除此索引，新库也不创建。`symbol` 唯一约束及证券类型、交易所索引保留。

## 7. market.trading_calendar

### 作用

记录交易所交易日历，用于判断缺失、节假日、停牌和异常交易日。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `exchange` | `varchar(10)` | 是 | 交易所 |
| `trade_date` | `date` | 是 | 日期 |
| `is_open` | `boolean` | 是 | 是否交易日 |
| `previous_trade_date` | `date` | 否 | 上一交易日 |
| `next_trade_date` | `date` | 否 | 下一交易日 |
| `is_half_day` | `boolean` | 是 | 是否半日交易，A 股一般为 false |
| `source_name` | `varchar(50)` | 否 | 来源 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

### 约束与索引

```sql
unique(exchange, trade_date)
index(trade_date)
index(exchange, is_open)
```

## 8. market.daily_bar

### 作用

可信日线表，供查询、因子、策略、回测使用。

字段契约见 `aiDoc/data/daily-bar-contract.md`。

### 约束与索引

```sql
unique(symbol, trade_date, adjust_type)
foreign key(selected_raw_id) references raw.daily_bar(id)
index(symbol, adjust_type, trade_date)
index(trade_date, security_type)
index(quality_status)
```

## 9. market.security_status_event

### 作用

记录每只股票在每个交易日的状态，作为区分“真实停牌”与“数据缺失”的事实源。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `symbol` | `varchar(20)` | 是 | 证券代码 |
| `trade_date` | `date` | 是 | 交易日期 |
| `status` | `varchar(30)` | 是 | `normal` / `suspended` / `st` / `star_st` / `delisting` / `unlisted` / `delisted` |
| `source_name` | `varchar(50)` | 是 | 数据来源 |
| `source_type` | `varchar(20)` | 否 | 来源类型 |
| `batch_id` | `bigint` | 否 | 关联 `raw.import_batch.id` |
| `raw_payload` | `jsonb` | 否 | 来源原始字段 |
| `effective_from` | `date` | 否 | 状态生效日 |
| `effective_to` | `date` | 否 | 状态失效日，NULL 表示仍有效 |
| `note` | `text` | 否 | 备注 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

### 约束与索引

```sql
unique(symbol, trade_date, status, source_name)
check(status in ('normal', 'suspended', 'st', 'star_st', 'delisting', 'unlisted', 'delisted'))
check(effective_from is null or effective_to is null or effective_from <= effective_to)
index(symbol, trade_date)
index(status, trade_date)
```

### 使用规则

- `market.daily_bar` 只保存可交易日行情，停牌日、未上市日、已退市日、非交易日均无 bar。
- 缺失检测发现交易日无 bar 时，先查本表：状态为 `suspended` / `delisting` 等属正常；`normal` 或缺失则生成 `MISSING_BAR`。
- 选股、回测、模拟推演应优先通过本表判断证券可交易性。

## 10. market.daily_bar_source_compare

### 作用

记录同一证券、同一日期、同一复权口径下，多来源之间的差异。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `symbol` | `varchar(20)` | 是 | 证券代码 |
| `trade_date` | `date` | 是 | 交易日期 |
| `adjust_type` | `varchar(10)` | 是 | 复权类型 |
| `source_a` | `varchar(50)` | 是 | 来源 A |
| `source_b` | `varchar(50)` | 是 | 来源 B |
| `raw_id_a` | `bigint` | 是 | 来源 A raw ID |
| `raw_id_b` | `bigint` | 是 | 来源 B raw ID |
| `close_diff` | `numeric(18,6)` | 否 | 收盘价差异 |
| `close_diff_pct` | `numeric(12,6)` | 否 | 收盘价差异比例 |
| `volume_diff_pct` | `numeric(12,6)` | 否 | 成交量差异比例 |
| `amount_diff_pct` | `numeric(12,6)` | 否 | 成交额差异比例 |
| `is_conflict` | `boolean` | 是 | 是否冲突 |
| `conflict_reason` | `text` | 否 | 冲突原因 |
| `created_at` | `timestamptz` | 是 | 创建时间 |

### 索引

```sql
index(symbol, trade_date, adjust_type)
index(is_conflict)
```

## 10. market.manual_correction

### 作用

记录人工修复审计。人工修复不能静默覆盖 `market.daily_bar`。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `dataset` | `varchar(50)` | 是 | 数据集，例如 `daily_bar` |
| `symbol` | `varchar(20)` | 是 | 证券代码 |
| `trade_date` | `date` | 是 | 交易日期 |
| `adjust_type` | `varchar(10)` | 是 | 复权类型 |
| `field_name` | `varchar(50)` | 是 | 修复字段 |
| `old_value` | `text` | 否 | 原值 |
| `new_value` | `text` | 是 | 新值 |
| `reason` | `text` | 是 | 修复原因 |
| `corrected_by` | `varchar(50)` | 是 | 修复人 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `applied_at` | `timestamptz` | 否 | 应用时间 |

## 11. metadata.data_quality_issue

### 作用

记录数据质量问题。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `dataset` | `varchar(50)` | 是 | 数据集 |
| `symbol` | `varchar(20)` | 否 | 证券代码 |
| `trade_date` | `date` | 否 | 交易日期 |
| `adjust_type` | `varchar(10)` | 否 | 复权类型 |
| `source_name` | `varchar(50)` | 否 | 来源名称 |
| `raw_id` | `bigint` | 否 | raw 记录 ID |
| `issue_type` | `varchar(50)` | 是 | 问题类型 |
| `severity` | `varchar(20)` | 是 | `info` / `warning` / `error` / `critical` |
| `description` | `text` | 是 | 问题描述 |
| `expected_value` | `jsonb` | 否 | 期望值 |
| `actual_value` | `jsonb` | 否 | 实际值 |
| `status` | `varchar(20)` | 是 | `open` / `ignored` / `fixed` / `false_positive` |
| `resolved_by` | `varchar(50)` | 否 | 处理人 |
| `resolved_at` | `timestamptz` | 否 | 处理时间 |
| `created_at` | `timestamptz` | 是 | 创建时间 |

### 索引

```sql
index(status)
index(symbol, trade_date)
index(issue_type)
index(severity)
```

## 12. metadata.job_log

### 作用

记录导入、清洗、校验、修复、调度任务。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `job_name` | `varchar(100)` | 是 | 任务名称 |
| `job_type` | `varchar(50)` | 是 | `import` / `normalize` / `validate` / `repair` |
| `batch_id` | `bigint` | 否 | 关联导入批次 |
| `started_at` | `timestamptz` | 是 | 开始时间 |
| `finished_at` | `timestamptz` | 否 | 结束时间 |
| `status` | `varchar(20)` | 是 | `running` / `success` / `failed` / `partial` |
| `rows_read` | `integer` | 是 | 读取行数 |
| `rows_inserted` | `integer` | 是 | 插入行数 |
| `rows_updated` | `integer` | 是 | 更新行数 |
| `rows_failed` | `integer` | 是 | 失败行数 |
| `error_message` | `text` | 否 | 错误信息 |
| `metadata` | `jsonb` | 否 | 额外信息 |
| `created_at` | `timestamptz` | 是 | 创建时间 |

`metadata` 中的 `_job_lease` 是基础设施保留键，业务写入使用它会被 repository 拒绝：新建 `running` 记录必须带 `_job_lease.version=process_slot_v1`，`finish` 不允许改写已写入的租约。死亡判定、自动收敛范围和操作人显式出口见 [系统关系](../relations/system-map.md) 的工具边界小节。

## 13. metadata.data_source

### 作用

记录数据源配置和默认优先级。

### 字段

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `source_name` | `varchar(50)` | 是 | 来源名称 |
| `source_type` | `varchar(20)` | 是 | 来源类型 |
| `priority` | `integer` | 是 | 默认优先级，越小越高 |
| `is_active` | `boolean` | 是 | 是否启用 |
| `description` | `text` | 否 | 说明 |
| `config` | `jsonb` | 否 | 非敏感配置 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

### 约束

```sql
unique(source_name)
```

## 14. V1 初始建表依赖顺序

以下保留 V1 初始依赖关系；当前各库初始化与兼容升级以 `app/database/` 代码为准，不是可直接执行的迁移脚本。

1. 创建 schema：`raw`、`market`、`metadata`。
2. 创建 `metadata.data_source`、`raw.import_batch`、`metadata.job_log`。
3. 创建 `raw.daily_bar`。
4. 创建 `market.security`、`market.trading_calendar`。
5. 创建 `market.daily_bar`。
6. 创建 `market.daily_bar_source_compare`。
7. 创建 `metadata.data_quality_issue`。
8. 创建 `market.manual_correction`。
9. 创建索引和约束。

## 15. V1 优化设计

真实采集开始后，数据库设计需要从“能建表”升级为“能长期跑”。V1 优化重点不是提前做复杂数仓，而是给当前链路补上约束、索引和演进边界。

### 15.1 约束分层

`raw` 层保留来源弹性，不对 OHLC、成交量、成交额做过强业务判断。原因是 raw 要承接外部脏数据和异常数据，后续由质量系统解释。

`market` 层必须强约束。进入可信消费层的数据应由数据库兜底保证：

- `adjust_type` 只能是 `none` / `qfq` / `hfq`。
- `quality_status` 只能是 `trusted` / `suspect` / `rejected` / `manual_fixed`。
- `quality_score` 必须在 `0-100`。
- OHLC 必须大于 0，且满足 `high >= open/close/low`、`low <= open/close/high`。
- `volume`、`amount` 如存在则不能为负。
- `source_count` 至少为 1。
- `market.daily_bar.symbol` 必须能在 `market.security.symbol` 中找到。

### 15.2 状态枚举

以下状态字段使用 check constraint 固化，避免后续服务随手写入不一致字符串：

```text
raw.import_batch.status: running / success / failed / partial
metadata.job_log.status: running / success / failed / partial
metadata.data_quality_issue.status: open / ignored / fixed / false_positive
metadata.data_quality_issue.severity: info / warning / error / critical
```

### 15.3 真实采集索引

同花顺、CSV、QMT 等来源开始持续导入后，`raw.daily_bar` 的高频访问不只是按 symbol 查询，还包括按来源、复权类型和日期窗口做批处理。因此增加：

```sql
index raw.daily_bar(symbol, adjust_type, trade_date)
index raw.daily_bar(source_name, symbol, adjust_type, trade_date)
```

### 15.4 回测查询索引

策略和回测最常见查询是“某证券 + 复权口径 + 日期区间 + 可信状态”。因此 `market.daily_bar` 保留通用索引：

```sql
index market.daily_bar(symbol, adjust_type, trade_date)
```

并增加面向默认消费路径的索引：

```sql
index market.daily_bar(quality_status, symbol, adjust_type, trade_date)
partial index market.daily_bar(symbol, adjust_type, trade_date)
where quality_status in ('trusted', 'manual_fixed')
```

### 15.5 审计和排障索引

质量问题和人工修复会成为后续数据运营界面的主要入口，因此增加：

```sql
index metadata.data_quality_issue(symbol, status, trade_date)
index metadata.data_quality_issue(status, severity, created_at)
index market.manual_correction(symbol, trade_date, adjust_type, field_name)
```

### 15.6 暂不做的优化

V1 暂不引入以下设计：

- 分区表：当前数据量尚未到需要按年份或月份分区的阶段。
- 版本化日线表：先用当前表 + raw 留痕 + manual correction 审计。
- raw 层严格去重唯一键：先允许完整留痕，等导入服务具备 upsert / conflict handling 后再决定是否启用。
- PostgreSQL enum 类型：先用 `varchar + check constraint`，便于早期演进。

## 16. 当前已扩展表域

以下表域已经不再是“暂不创建”，而是当前代码的一部分：
- `factor.*`：因子定义、运行批次、因子值、横截面 profile。`factor.factor_definition` 新增非空 `category` 字段（11 个决策域分类，见 `aiDoc/research/decision-factor-framework.md`）与 `ix_factor_definition_category` 索引；派生因子（`xxx__transform`）继承基础因子分类，历史设计曾引用 Alembic `20260722_0024`，当前仓库无该迁移；新注册及派生定义由服务写入/继承分类，既有数据不能仅凭旧迁移编号认定已回填。
  - **2026-10-26 新增两列**：`factor_definition.pit_status`（`point_in_time` / `look_ahead` / `unknown`，默认 `unknown`）与 `factor_definition.status`（`active` / `retired`，默认 `active`）。两者是因子池分级器的硬闸门输入：`look_ahead` 与 `retired` 的定义硬冻结，`unknown` 不得进入核心池。`pit_status` 与 `status` 互不等价于 `is_active`——`is_active` 表示"是否参与批量画像"，`status` 表示"该定义是否还是活的证据"；IC 证据由专用脚本产出的定义因此 `is_active=False` 但 `status='active'`。
  - **`factor.factor_profile` 扩展指标列**：`long_short_forward_return_5d`、`long_short_forward_return_20d`（与既有 `long_short_forward_return_1d` 同义）与 `quantile_monotonicity_1d` / `top_quantile_turnover` 的落库保障。端点价差（top−bottom 分位）是极端分位收益方向的必要研究证据之一，不能单独证明可交易性，分级器以 1 日端点严格为正作为进入核心池的硬闸门。
  - **证据资格（2026-10-26）**：`factor.factor_profile.evidence_eligibility`（`strategy_eligible` / `research_only`，默认 `research_only`）和 `evidence_eligibility_reason` 附着于每日证据，而不是因子定义。汇总时只要任一日不是 `strategy_eligible` 即保持研究限定；核心池同时要求定义 PIT 合格和证据资格合格。这样未来同一定义用真实 PIT 快照重算时能获得资格，而今天补采的历史行不会被误判为可交易。
  - 这些列由启动兼容桥 `app/database/session.py::apply_schema_compatibility` 幂等补齐（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + 既有行回填默认值），项目当前**没有** Alembic 迁移目录，schema 变更走该兼容桥。
- `strategy.*`：回测运行、成交、净值、持仓和绩效。**2026-10-24 起这 6 张表物理上位于独立 `data/research.duckdb`**（模型挂 `ResearchBase`，服务层 `research_db=None` 兼容参数贯穿，迁移脚本 `migrate_strategy_to_research.py`；见 `aiDoc/relations/20261024-1427-research-database-split.md`），**2026-11-11 起同一物理库新增第 7 张表 `strategy.external_observation`**（外部连接器观测快照，同样挂 `ResearchBase`，模型 `app/models/strategy/external_observation.py`，设计见 `aiDoc/relations/20261111-1415-a-stock-data-integration-assessment.md`），schema 限定名 `strategy.*` 不变，SQL/ORM 查询写法不变。新库训练产物关联 `backtest_run.training_artifact_id -> training_artifact.id` 使用默认外键删除限制，禁止删除被回测引用的产物，允许空引用；DuckDB 不支持 `ON DELETE SET NULL`，不能依赖它静默断开血缘。启动兼容桥为旧库幂等添加 nullable 的 `training_artifact_id` 和索引，保留已有回测记录；旧表追加列不追补外键，启用训练产物绑定前仍需正式约束迁移。当前独立滚动入口已支持不可变训练产物绑定与逐测试窗执行，并可启用训练窗自动选参；合同仍标记 `training_artifact_verified=false`，不能将产物绑定等同输入证据或策略资格认证。
  - 价格价值假设回测复用上述表，无新增 schema 或迁移。`backtest_run.parameters` 保存 `price_value_assumption_v1` 合同、显式假设、市场快照、引擎版本、输入哈希及完整 `price_value_report`（含决策日志）；`validation_mode=none`、`pit_verified=false`。标准成交/净值/持仓保持原定点精度，JSON 报告保留 Decimal 字符串精度；写入仅是研究存储，不创建 `trade.*` 事实。
  - **`strategy.strategy_definition`（2026-11-09 新增，主库）**：内置策略定义目录，模型挂 `Base`，与上述研究库的 7 张表分属两个物理库。字段：`name` + `version`（唯一键）、`display_name`、`description`、`category`、`parameters_schema`（JSON，参数契约）、`parameters`（JSON，锁定阈值的**取值**）、`locked_parameters`（JSON，锁定参数名清单）、`required_margin`（`NUMERIC(12,10)`，锁定的安全边际）、`inputs`（JSON，数据来源列路径）、`is_active`、`status`（`active` / `retired`）。约束：`required_margin` 为空或落在 `(0, 1)`。
    - `parameters` 列（2026-11-10 新增，nullable JSON）：承载除 `required_margin` 外的全部锁定阈值取值。`required_margin` 刻意**不放**在这份 JSON 里——同一个数字存两份可能互相矛盾的副本，参数就失去锁定意义。Decimal 一律用字符串（`"0.08"`），计数用整数。列可空是因为它是通过兼容桥加到既有库上的（DuckDB 的 `ALTER TABLE ADD COLUMN` 没法补 `NOT NULL`），读取时把缺失视为「没有额外阈值」。新库由 `create_all` 建成，与兼容桥路径一致。
    - 落主库而不是研究库是决定性的：策略定义必须在**每次调用**时被读取，而研究库在研究脚本写入期间会对任何读请求返回 409；放进研究库会让内置策略在研究作业运行时整体不可用。
    - 不复用 `factor.factor_definition`：那张表是**因子目录**，其行会被批量画像与因子池分级器消费，塞入策略会产生假因子。也不复用 `strategy.training_artifact`：它的主键是 `window_id`（一行一窗口），承载不了可跨窗口反复调用的策略定义。
    - 由 `app/strategies/registry.py::register_all_strategy_definitions` 按 `(name, version)` 幂等 upsert；`app/repositories/strategy/definition.py` 提供 `ensure()` / `get_by_name_version()` / `get_active()` / `list()`。
    - 新表由 `Base.metadata.create_all` 幂等创建，不新增 Alembic 迁移；当前 schema 变更仍走 `app/database/session.py::apply_schema_compatibility` 兼容桥。
    - **版本演进（2026-11-10）**：`@v1`（仅价格闸门）与 `@v2`（价格 + 质地闸门）均置 `status='retired'` 保留供审计，`@v3` 为 `status='active'`。`@v3` 的 `parameters` 携带 4 个新增锁定阈值：`min_revenue_cagr_3y=0.12`、`min_net_profit_cagr_3y=0.15`、`decline_streak_years=2`、`max_debt_ratio_rise_3y=0.10`。`required_margin` 在三个版本中恒为 `0.30`。
  - **`strategy.external_observation`（2026-11-10 新增，研究库）**：外部连接器观测的**可审计快照表**，模型挂 `ResearchBase`（物理上在 `backend/data/research.duckdb`，与上方回测产物 6 表同库，研究库合计 7 表）。字段：`symbol`、`source`、`observation_type`、`as_of_date`、`captured_at`、`payload`（JSON）、`payload_version`；唯一键 `(symbol, source, observation_type, as_of_date)`，按 `(symbol, source, observation_type, as_of_date)` 幂等 upsert。
    - **这张表永不被任何闸门读取**。外部数据（券商一致预期、机构评级、研报观点）没有「首次可得时间」，本身就是前瞻性预测；把它接入闸门会让 PIT 约束形同虚设。表的存在意义是**留存证据供审计**，闸门证据仍只来自控制面与温层的事实数据。
    - 读取与渲染在 `app/services/value_investing_research_card.py`：装配成研究卡，与已实现事实分区呈现，一致预期统一放在 `forward_looking` 块并固定附带「机构一致预期是前瞻性预测，不是已实现事实，不参与本策略的任何闸门判定」警示语。**没有观测的证券不出卡**，避免把「看过但没找到」与「没看过」混为一谈。
    - 写入脚本 `backend/scripts/research/capture_value_investing_observations.py`（`--observations` 读连接器原始 JSON，`--include-trap-examples` 只收「陷阱闸门是唯一否决理由」的标的）。
- `raw.daily_bar / market.daily_bar`：估值研究依赖 `limit_up / limit_down / is_suspended / special_treatment_status`，从选定 raw 记录复制至可信日线。沿用当前 DuckDB 初始化兼容桥幂等补列，旧行限价和特殊处理状态保留 NULL，停牌字段沿用 false 默认值；默认值不证明历史可交易，研究仍要求明确普通状态和有效限价。既有值不覆盖，实际用户数据库未由本次开发直接升级。
- `market.security_status_event`：记录每只股票在每个交易日的状态（正常、停牌、ST、*ST、退市整理、未上市、已退市等），作为区分“真实停牌”与“数据缺失”的事实源。`market.daily_bar` 只保存可交易日行情，停牌日无 bar；下游应通过本表判断证券可交易性。
- `portfolio.*`：组合计划和目标持仓。`portfolio.portfolio_plan` 以 nullable 的 `idempotency_key`、`request_hash` 和 `input_snapshot` 兼容历史记录；新公共草案 API 要求幂等键，保存规范化手工输入快照，并通过唯一索引防止重试生成重复草案。`portfolio_plan.parameters` 记录组合约束口径，当前包含 `enforcement_mode`（`proportional_scale` / `score_ranked_trim`，默认等比缩放）和 `extra_symbol_caps`（单标的额外上限字典），用于回放与审计。`portfolio_plan.style_risk JSON` 将 Decimal 数值编码为十进制字符串，保存绝对风格暴露、历史风险指标、因子日期/版本/运行 ID/截面覆盖数和收益样本范围；旧记录 NULL 表示未记录，不作补算。草案 API 将现金约束到 DECIMAL(24,4)、输入价约束到 DECIMAL(18,6)，避免计算值与落库精度分离。`portfolio_target` 行记录 `requested_weight` / `constrained_weight` / `final_weight`、`score`（可选，记录择优截断排序依据）及调整原因。既有主库可能缺 `portfolio_plan.style_risk JSON` 与 `portfolio_target.score DECIMAL(18,10)`；启动兼容桥逐列、分事务幂等补齐，两列均 nullable，旧记录保持 NULL。2026-11-17 已对本机主库执行该定向升级并核对结构及只读草案列表；不对历史草案补算风格风险或分数。
- `trade.*`：**仅承载本地模拟推演账户与持仓记录，不含任何真实券商表或真实账户写操作流水**；`paper_account_equity_snapshot` 按账户/估值日 append-only 保存现金、市值、总权益、观测高水位、回撤、历史连续状态和三类哈希，`paper_account_equity_component` 复制逐持仓数量、成本、未复权价格及行情质量血缘。旧历史不回填，首条固定为 `bootstrap`。`paper_execution_approval` 一对一绑定通过的正式风险评估，保存代码版本、评估、账户、净值、readiness、ADV、政策和规范化订单批次哈希；状态限定为 `pending -> consumed | expired | revoked`，创建和撤销分别使用唯一幂等键。`paper_execution_batch` 以 approval 和执行幂等键双唯一约束保存代码版本、前后账户状态、执行政策、输入/结果快照和稳定失败码，并以 check constraint 约束 completed/rejected 的计数、现金、hash 和时间不变量；新 `paper_order` 通过 nullable 的 batch、risk order 和 execution index 兼容旧记录，`paper_fill` 限定每个同步 full-fill 订单一条成交。
- `risk.*`：`risk_check` 保留旧行 nullable 兼容，并为新正式评估绑定 account、equity snapshot、plan/readiness/ADV/policy/evaluation 哈希及幂等键；`risk_check_order` 复制参考价、投影权重、服务端 ADV 20/15 输入和逐规则结果。风险记录不等于交易订单。
- `fundamental.*`：财务报表与指标。
- `market.alternative_data_snapshot`、`market.index_constituent_snapshot` 和 `market.valuation_snapshot`：另类数据、指数成分和估值快照。
- `lake.release`：新 `release_id` 由数据集、checksum、schema 版本、日期范围、行数、父 release、代码版本和元数据共同取 SHA-256，绑定内容与不可变血缘；相同事实重跑复用原行。升级前按 `dataset + checksum` 生成的 16 位 ID 保留，且在所有不可变字段相同时继续复用；字段不同才生成新 ID，不改写旧行。表同时记录质量状态、文件 manifest 和 active 指针。`factor.factor_run.release_id` 绑定因子输入 release，`market.adjustment_factor.release_id` 绑定复权血缘。现有 DuckDB 由启动/日循环入口执行幂等兼容升级，为这两个既有表补充 nullable `release_id`；新表仍由 SQLAlchemy metadata 创建。
- `positions`（独立 SQLite，`data/positions.db`）：手工记账的真实持仓账本（决策见 `aiDoc/relations/20261023-1925-position-ledger-sqlite.md`），与智能体 SQLite 状态库分别保存不同事实。`position_book` 记账本名与现金（`cash_quants` 整数最小单位 0.0001 元，满足定点要求），`position_trade` 记买卖流水（side/quantity/price_quants/fee_quants/trade_date/幂等键）。持仓数量、平均成本与已实现盈亏由流水按 `(trade_date, id)` 重放推导，不落库；估值价格取 DuckDB `market` 可信层，缺价时按成本估值并标记 `price_stale`。卖出校验净持仓非负，删除流水后重放校验不产生负持仓。
- `market.security_industry_history`（模型 `app/models/market/security_industry_history.py`）：行业的时点事实源，字段 `symbol` / `industry` / `effective_from` / `effective_to`（NULL 为开放区间）/ `source_name`；`(symbol, effective_from)` 唯一，窗口查询走 `ix_security_industry_history_window`。`market.security.industry` 只承载**当前标签**，历史因子若直接读它就是把今天的分类倒填进过去；生产 `source_name='sw_industry_history'`（申万归属变动表，2026-11-11 起）。2026-10-24 那批 `source_name='current_label_backfill_20261024'` 的假区间（每标的一条、`effective_from` 即上市日、`effective_to` 全 NULL）只在申万覆盖到的标的上被删除重写，未覆盖标的保留原回填行并靠 `source_name` 区分，不会被当成时点事实。写入只走 `app/repositories/market/security_industry_history.py::record_interval`（关旧区间再插新区间），采集服务 `app/services/industry_history_collection.py`，决策见 `aiDoc/relations/20261111-1810-industry-point-in-time-history.md`。
- `fundamental.financial_statement_revision`（模型 `app/models/fundamental/financial.py:85`）：`financial_statement` 只保留同一报表键的最新内容，本表在内容变化（更正/重述/补公告日）前保存上一版本，`revision_no` 从 1 起按内容变化递增，唯一键 `(symbol, statement_type, period, fiscal_year, fiscal_period, revision_no)`。因子运行绑定 `statement_content_digest` 与修订绑定元数据，因此历史运行可审计到当时可见的报表内容。
- `raw.corporate_action` / `market.corporate_action`、`raw.financial_payload`、`raw.vendor_snapshot`：公司行为、财务 payload 与供应商原始快照的 raw 留痕层，全部只进 `raw`，由对应采集服务写批次，可信消费仍走 `market` / `fundamental`。证据补采（公司行为 / 换手率 / 退市名单）每轮都留 `raw.import_batch` 与 `raw.vendor_snapshot`。
- `factor.factor_value`：因子值长表，唯一键 `(symbol, trade_date, factor_name, factor_version)`，值列 `value` 为 `NUMERIC(24,10)` 非空，并带写入批次 `run_id`；与 `factor.factor_run`、`factor.factor_profile` 一起构成可复现因子事实，`factor.factor_run.release_id` 绑定温层输入 release。
- `trade.paper_position`：模拟推演账户的当前持仓行，与 `paper_account` / `paper_order` / `paper_fill` / 净值快照及组件同域，仍只是本地推演结果，不对应任何真实账户持仓；真实持仓账本在 `positions` 域。

## 17. 温层 Parquet 研究层表

除 `raw / market / factor / fundamental` 等控制面表域外，**研究层**还有一类只存在于 Parquet 温层的表。它们不是控制面表的子集，而是独立的研究数据集。

### 17.1 判定标准（四要素齐备才算温层研究表）

一张表同时满足以下四条，才归入温层 Parquet 研究层表：

1. **只存在于 Parquet**：物理文件在 `backend/data/warm/exported/<release_tag>/<schema>/<table>/`，控制面 DuckDB（`data/stock_master.duckdb`）里**没有**对应的基表。
2. **无 ORM 模型**：没有 `app/models/**` 下的 SQLAlchemy 模型类，也不参与 `Base.metadata.create_all`。
3. **无 Alembic 迁移**：没有对应的 revision，也没有 `apply_schema_compatibility` 的幂等 ALTER。
4. **不出现在控制面 `information_schema`**：`SELECT * FROM information_schema.tables WHERE table_catalog='ctrl'` 查不到它。

**读取入口**统一为 `WarmLayerStore.connect()` 返回的 DuckDB 连接，配 `app/repositories/research_read.py` 的 `WarmResearchReadRepository`。

### 17.2 现有温层研究表

| schema.table | 内容 | 关键列 | 读取入口 |
|---|---|---|---|
| `research.valuation_history` | 东财估值历史序列（PE/PB/PS、总市值、总股本、流通股本、流通市值、涨跌幅） | `symbol`、`trade_date`、`close`、`pe_ttm`、`pb_mrq`、`ps_ttm`、`total_market_cap`、`total_shares`、`free_shares_a`、`float_market_cap`、`change_rate`、`pit_verified`、`source_name`、`observed_at` | `WarmResearchReadRepository.list_valuation_history` / `valuation_history_coverage` |

`research.valuation_history` 之所以放在 `research` 命名空间而不是 `market`，是因为它的行是**今天采集的历史数据**，不是当时可知的证据：全部行的 `pit_verified=false`。它不得被 `market.valuation_snapshot` 的通用读取器拾取，也不得写入控制面。

### 17.3 与可信层的关系

- 温层研究表**不是**可信层表的替代品。它们服务于「研究口径」而非「交易口径」，消费方向仍然是 `raw -> market -> factor -> research/backtest`。
- 温层研究表在 ORM 侧有**孪生读取路径**（见 `aiDoc/modules/backend-layer-rules.md`）：`research.valuation_history` 的 ORM 孪生是 `app/repositories/research_valuation.py` 的 `ResearchValuationReadRepository`，读控制面的 `market.valuation_snapshot`。两条路径的列名、排序和过滤语义一致，但覆盖范围不同：控制面只有实际采集过的交易日，温层才有完整回填。
- 字段级时点有效性差异必须显式暴露：温层的 `pb_mrq` 会前视最多 120 天，ORM 孪生因此把 `pb_mrq` 报为 `None`。温层保持原始 PIT 标记（现有历史补采均为 `false`）；ORM 没有字段级首次可得证据，默认 `pit_verified=false`，并排除 `_research_batch_id` 研究观察，覆盖统计也使用同一过滤。两条读取路径保留 `source_name` 并按日期、证券、来源排序，不静默合并多源同日记录。此次无表结构变更。

组合草案 `parameters` JSON 还保存可选 `style_factor_map` 与 `benchmark_symbol`，详情回读这些请求约束；旧记录缺键时保持空值。

仍暂不创建或不作为当前主线：

- 分钟线、Tick、逐笔成交表。
- 真实券商写操作流水与自动化交易相关表（永久不在项目范围内）。
- 严格版本化的 `market.daily_bar_version`。

新增表域必须继续遵守 `raw -> market -> factor -> research/backtest -> portfolio/risk -> trade` 的消费方向，不能让策略、回测或持仓推演链路直接绕过可信层。
