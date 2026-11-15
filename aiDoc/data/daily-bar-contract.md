# 日线数据字段契约

本文定义 Stock Master V1 的标准日线数据契约。后续 SQLAlchemy 模型、Alembic 迁移、CSV 导入、质量校验、API 返回结构都应以本文为基础。

估值研究补充交易状态字段：`limit_up / limit_down` 为同币种、同日期未复权限价；`is_suspended` 为停牌标志；`special_treatment_status` 为历史特殊处理状态，普通状态用 `normal` 或 `none`。字段随选定 raw 行保留至 market，不从低价或涨跌幅推断。旧行限价与特殊处理状态为 NULL，停牌标志沿用 false 默认值；默认值不构成历史可交易认证，价格价值回测缺少限价或明确普通状态时拒绝运行。DuckDB 初始化兼容桥幂等补列且不覆盖已有值；来源适配器的自动补采仍需后续接入。

**涨跌停推导（2026-10-22）**：控制面 `market.daily_bar` 现有 325,774 行里 `limit_up` / `limit_down` **全部为 NULL**，供应商字段不可用。研究读路径改由 `app/market/price_limit.py` 从当日 `pre_close` 按板块推导：主板（600/601/603/605/000/001/002/003）±10%，创业板（300/301）与科创板（688）±20%，北交所（43/83/87/88/920）±30%，B 股（900/200）±10%；主板 ST/*ST 降为 ±5%（向下不对称，向上仍 10%）。金额 ROUND_HALF_UP 到分。锚点必须是**当日自身的 `pre_close`**，不能用前一行的 `close`。指数（`.SH` 且 `000` 前缀，如 `000300.SH`、`000001.SH`）与非股票不套用涨跌幅带，返回 NULL。校验不变式是 `high <= limit_up` 且 `low >= limit_down`，剔除上市前 5 个交易日后真实违规率 0.092%（残差来自 `pre_close` 自身质量离群值）；不要用「涨跌幅 ≈ 10%」反推，收盘涨 10% 不等于收盘价等于涨停价。

**停牌数据边界（2026-10-22 收紧）**：`market.daily_bar` 只保存可交易日行情。停牌证券在停牌日**不应**出现 `market.daily_bar` 记录；`is_suspended` 在 market 层应恒为 `false`。停牌、ST、*ST、退市整理、未上市、已退市等状态由独立的 `market.security_status_event` 表记录，作为区分“真实停牌”与“数据缺失”的事实源。下游选股、回测、模拟推演应优先通过该状态表判断证券可交易性，而不是通过 `daily_bar.is_suspended`。

## 1. 适用范围

日频因子回测的持仓估值采用 `reject_on_open_date_v1`（引擎 `daily_event_v4`，`app/backtest/engine.py:16`）：已持仓证券在自身交易所开市日必须有可信日线，缺行立即失败，错误包含证券和日期；不以最后收盘价静默填补未知断档。闭市日可沿价。当前尚未把可信停牌事件接入该引擎的携价例外，因此即使市场实际停牌，缺行也保持阻断；价格价值专用引擎的显式停牌处理另按其合同执行。

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
| `trusted` | 已通过校验，可供消费（数值校验 + 采集时点校验均通过） |
| `suspect` | 可疑，可展示但默认不用于严格回测；也承载「盘中采集的未定稿 bar」 |
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
| `source_count` | `integer` | 是 | `max(1, 该键最新且实际参与结算后对账的唯一来源名称数)`；1 是 schema 保底，盘中快照不增加计数，也不表示已有一个结算后来源 |
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
2. `trade_date` 是对应交易所交易日；停牌日、未上市日、已退市日、非交易日均不得进入。
3. `adjust_type` 属于允许枚举。
4. `open`、`high`、`low`、`close` 非空且大于 0。
5. `high >= open`、`high >= close`、`high >= low`。
6. `low <= open`、`low <= close`、`low <= high`。
7. `volume` 和 `amount` 不得为负；任一字段缺失，或一项为零而另一项为正，只能进入 `suspect`。两项同时为零目前不自动判错（现有可信样本仅见于指数），stock/ETF 是否需要降级须以独立来源和交易状态证据另行定规则。停牌日无 bar，因此不存在“停牌日成交量为零”的 market 记录。
8. 若 `pre_close` 存在，则 `change_amount` 和 `pct_change` 应可解释。
9. 若存在多源冲突且超过阈值，不应自动标记为 `trusted`。
10. `is_suspended` 必须为 `false`；任何来源标记为停牌的 raw 记录不得直接进入 market，应导入 `market.security_status_event` 并生成质量问题。
11. **采集时点**：所选 raw 行的 `imported_at` 必须不早于该 bar 自身交易日的 `15:30 Asia/Shanghai`，否则只能记为 `suspect`（`INTRADAY_SNAPSHOT` + 每交易日一行 `INTRADAY_SNAPSHOT_BATCH`）。盘中快照数值自洽，只有出处能证伪它；缺失 `imported_at` 不构成盘中采集的证据，不降级。详见 `aiDoc/relations/20261111-0300-intraday-snapshot-guard.md`。
12. 同一业务键的最新 raw 修订若命中停牌、非法 OHLC、负量额等硬拒绝，已有非人工修复 market 行必须降为 `rejected`、`is_suspect=true`、质量分 0，保留旧价格和 `selected_raw_id` 供审计但立即退出可信消费；不得因旧版本曾经可信而继续供因子或回测读取。`manual_fixed` 仍由独立人工审计保护。大包快速路径必须把全部硬拒绝键交回公共质量构建，不能只靠插入 SQL 过滤后留下旧 trusted 行。

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
