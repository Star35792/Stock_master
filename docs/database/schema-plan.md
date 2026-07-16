# 数据库 Schema 规划

本文是 V1 可信日线数据中心的数据库规划，后续 SQLAlchemy 模型和 Alembic migration 应以本文为依据。

## 1. 设计原则

1. **分层存储**：原始数据、可信数据、系统审计分 schema 存放。
2. **先留痕再消费**：外部数据先进 `raw`，不能直接写入 `market`。
3. **字段标准化**：`market` 层字段必须符合 `docs/data/daily-bar-contract.md`。
4. **质量可追踪**：导入、校验、冲突、修复都必须有记录。
5. **首批克制**：V1 只做日线数据中心，不提前创建完整因子、策略、实盘交易表。

## 2. Schema 划分

首批核心 schema：

```text
raw       原始导入层
market    可信市场数据层
system    任务、质量、审计层
```

当前 `app/database/base.py` 已包含多个长期 schema，后续实现时需要确保包含 `raw`：

```python
SCHEMAS = (
    "raw",
    "market",
    "fundamental",
    "factor",
    "strategy",
    "portfolio",
    "trade",
    "system",
)
```

V1 只实际落地 `raw`、`market`、`system`。

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

system.job_log
system.data_quality_issue
system.data_source
```

其中最小可运行闭环至少需要：

```text
raw.import_batch
raw.daily_bar
market.security
market.daily_bar
system.data_quality_issue
system.job_log
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
index(name)
```

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

字段契约见 `docs/data/daily-bar-contract.md`。

### 约束与索引

```sql
unique(symbol, trade_date, adjust_type)
foreign key(selected_raw_id) references raw.daily_bar(id)
index(symbol, adjust_type, trade_date)
index(trade_date, security_type)
index(quality_status)
```

## 9. market.daily_bar_source_compare

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

## 11. system.data_quality_issue

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

## 12. system.job_log

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

## 13. system.data_source

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

## 14. 迁移顺序建议

1. 创建 schema：`raw`、`market`、`system`。
2. 创建 `system.data_source`、`raw.import_batch`、`system.job_log`。
3. 创建 `raw.daily_bar`。
4. 创建 `market.security`、`market.trading_calendar`。
5. 创建 `market.daily_bar`。
6. 创建 `market.daily_bar_source_compare`。
7. 创建 `system.data_quality_issue`。
8. 创建 `market.manual_correction`。
9. 创建索引和约束。

## 15. 暂不创建的表

V1 暂不创建：

- `factor.*` 因子表。
- `strategy.*` 策略和回测表。
- `portfolio.*` 组合和持仓表。
- `trade.*` 实盘订单和成交表。
- 分钟线、Tick、逐笔成交表。
- 财务全量表。

等日线可信数据中心跑通后再按路线图扩展。
