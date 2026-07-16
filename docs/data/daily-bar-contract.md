# 日线数据字段契约

本文定义 Stock Master V1 的标准日线数据契约。后续 SQLAlchemy 模型、Alembic 迁移、CSV 导入、质量校验、API 返回结构都应以本文为基础。

## 1. 适用范围

首批覆盖：

- A 股股票。
- ETF。
- 指数日线可按同一契约扩展。

首批不覆盖：

- 分钟线。
- Tick。
- 逐笔成交。
- 财务报表。

## 2. 命名原则

- 证券代码统一使用 `symbol`，例如 `600519.SH`、`000001.SZ`、`159915.SZ`。
- 交易日期统一使用 `trade_date`。
- OHLC 字段使用行业惯例：`open`、`high`、`low`、`close`。
- 涨跌额使用 `change_amount`，避免使用含义模糊的 `change`。
- 涨跌幅使用 `pct_change`，单位为百分比数值，例如 `1.25` 表示上涨 1.25%。
- 复权类型使用 `adjust_type`。

## 3. 枚举值

### 3.1 security_type

| 值 | 含义 |
|---|---|
| `stock` | 股票 |
| `etf` | ETF |
| `index` | 指数 |
| `fund` | 基金，后续扩展 |
| `bond` | 债券，后续扩展 |
| `unknown` | 未知 |

### 3.2 exchange

| 值 | 含义 |
|---|---|
| `SH` | 上海证券交易所 |
| `SZ` | 深圳证券交易所 |
| `BJ` | 北京证券交易所 |
| `HK` | 港交所，后续扩展 |
| `UNKNOWN` | 未知 |

### 3.3 adjust_type

| 值 | 含义 |
|---|---|
| `none` | 不复权 |
| `qfq` | 前复权 |
| `hfq` | 后复权 |

### 3.4 quality_status

| 值 | 含义 |
|---|---|
| `trusted` | 已通过校验，可供消费 |
| `suspect` | 可疑，可展示但默认不用于严格回测 |
| `rejected` | 已拒绝，不进入可信消费路径 |
| `manual_fixed` | 人工修复后采用 |

## 4. market.daily_bar 标准字段

`market.daily_bar` 是策略、回测和查询 API 使用的可信日线表。

| 字段 | 类型建议 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `bigserial` | 是 | 主键 |
| `symbol` | `varchar(20)` | 是 | 标准证券代码，例如 `600519.SH` |
| `security_type` | `varchar(20)` | 是 | `stock` / `etf` / `index` |
| `trade_date` | `date` | 是 | 交易日期 |
| `adjust_type` | `varchar(10)` | 是 | `none` / `qfq` / `hfq` |
| `open` | `numeric(18,4)` | 是 | 开盘价 |
| `high` | `numeric(18,4)` | 是 | 最高价 |
| `low` | `numeric(18,4)` | 是 | 最低价 |
| `close` | `numeric(18,4)` | 是 | 收盘价 |
| `pre_close` | `numeric(18,4)` | 否 | 前收盘价 |
| `change_amount` | `numeric(18,4)` | 否 | 涨跌额 |
| `pct_change` | `numeric(12,6)` | 否 | 涨跌幅，百分比口径 |
| `volume` | `numeric(24,4)` | 否 | 成交量，标准单位为股/份 |
| `amount` | `numeric(24,4)` | 否 | 成交额，标准单位为元 |
| `turnover_rate` | `numeric(12,6)` | 否 | 换手率，百分比口径 |
| `amplitude` | `numeric(12,6)` | 否 | 振幅，百分比口径 |
| `source_name` | `varchar(50)` | 是 | 最终采用的数据源名称 |
| `source_count` | `integer` | 是 | 参与对账的数据源数量 |
| `quality_score` | `numeric(5,2)` | 是 | 数据质量评分，0-100 |
| `quality_status` | `varchar(20)` | 是 | `trusted` / `suspect` / `rejected` / `manual_fixed` |
| `is_suspect` | `boolean` | 是 | 是否可疑 |
| `selected_raw_id` | `bigint` | 否 | 最终采用的原始记录 ID |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

## 5. 唯一键与查询索引

### 5.1 唯一键

```sql
unique(symbol, trade_date, adjust_type)
```

原因：同一证券、同一交易日、同一复权口径只能有一条当前可信记录。

### 5.2 关键索引

策略和回测最常用查询：

```sql
select *
from market.daily_bar
where symbol = '600519.SH'
  and adjust_type = 'qfq'
  and trade_date between '2020-01-01' and '2026-07-16'
order by trade_date;
```

推荐索引：

```sql
(symbol, adjust_type, trade_date)
(trade_date, security_type)
(quality_status)
```

## 6. 价格和数值精度

核心价格、成交量、成交额不要使用浮点类型保存。

PostgreSQL 建议：

- 价格：`numeric(18,4)`。
- 涨跌幅、换手率、振幅：`numeric(12,6)`。
- 成交量、成交额：`numeric(24,4)`。
- 质量分：`numeric(5,2)`。

Python 清洗和校验阶段如涉及精确比较，应使用 `Decimal` 或明确四舍五入规则。

## 7. 单位约定

不同来源单位可能不同，进入 `market` 前必须统一：

| 字段 | market 标准单位 |
|---|---|
| `volume` | 股 / 份 |
| `amount` | 元 |
| `pct_change` | 百分比数值，例如 1.25 |
| `turnover_rate` | 百分比数值 |
| `amplitude` | 百分比数值 |

`raw` 层可保存来源原始单位，但 `market` 层必须统一。

## 8. 基础质量约束

进入 `market.daily_bar` 的记录至少满足：

1. `symbol` 非空且能在 `market.security` 中找到。
2. `trade_date` 是对应交易所交易日，或者有明确停牌/特殊处理说明。
3. `adjust_type` 属于允许枚举。
4. `open`、`high`、`low`、`close` 非空且大于 0。
5. `high >= open`、`high >= close`、`high >= low`。
6. `low <= open`、`low <= close`、`low <= high`。
7. `volume` 和 `amount` 不得为负。
8. 若 `pre_close` 存在，则 `change_amount` 和 `pct_change` 应可解释。
9. 若存在多源冲突且超过阈值，不应自动标记为 `trusted`。

## 9. raw 与 market 的字段差异

`raw.daily_bar` 可以包含：

- 原始字段名。
- 原始单位。
- 原始代码格式。
- 原始 payload。
- 异常、重复、冲突数据。

`market.daily_bar` 必须包含：

- 标准字段名。
- 标准单位。
- 标准证券代码。
- 质量状态。
- 来源追溯。

## 10. API 返回建议

查询 API 可直接基于 `market.daily_bar` 返回：

```json
{
  "symbol": "600519.SH",
  "trade_date": "2026-07-16",
  "adjust_type": "qfq",
  "open": "1680.1200",
  "high": "1700.0000",
  "low": "1668.0000",
  "close": "1692.5000",
  "volume": "12345678.0000",
  "amount": "2100000000.0000",
  "quality_status": "trusted"
}
```

数值在 API 层可以按字符串返回，避免前端或客户端浮点误差；是否转成 number 由 API 设计单独决定。
