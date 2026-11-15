# 数据质量校验与修复设计

可信数据中心的可靠性不来自某个数据源，而来自可重复的数据质量校验、可解释的冲突处理和可审计的人工修复。

## 1. 校验目标

V1 数据质量系统需要发现：

- 字段缺失。
- 重复日线。
- OHLC 价格关系错误。
- 成交量、成交额异常。
- 交易日历不匹配。
- 停牌或缺口异常。
- 涨跌幅异常。
- 多源冲突。
- 复权口径不一致。
- 证券基础信息缺失。

## 2. 问题等级

| severity | 含义 | 处理建议 |
|---|---|---|
| `info` | 提示 | 不阻断入库，用于报告 |
| `warning` | 可疑 | 可进入 `market`，但标记 `suspect` |
| `error` | 错误 | 不应进入 trusted market |
| `critical` | 严重 | 可能影响既有可信数据，必须人工确认 |

## 3. 质量状态

`market.daily_bar.quality_status` 建议使用：

| 状态 | 含义 |
|---|---|
| `trusted` | 已通过校验，可供策略和回测使用 |
| `suspect` | 有问题但未被拒绝，默认不用于严格回测 |
| `rejected` | 被拒绝，不进入可信消费路径 |
| `manual_fixed` | 经人工修复后采用 |

## 4. 校验规则

### 4.1 字段完整性

必须检查：

- `symbol` 是否为空。
- `trade_date` 是否为空。
- `adjust_type` 是否为空或非法。
- `open/high/low/close` 是否为空。
- `security_type` 是否可识别。

问题类型：

```text
FIELD_MISSING
SECURITY_NOT_FOUND
ADJUST_TYPE_INVALID
```

### 4.2 OHLC 价格关系

规则：

```text
high >= open
high >= close
high >= low
low <= open
low <= close
low <= high
open > 0
high > 0
low > 0
close > 0
```

问题类型：

```text
PRICE_INVALID
```

### 4.3 成交量和成交额

规则：

```text
volume >= 0
amount >= 0
```

`volume` 或 `amount` 缺失时分别生成 `VOLUME_MISSING`、`AMOUNT_MISSING`，该行
只能进入 `suspect`，不得因走全量 dump 的批量构建路径而获得 `trusted`。成交量为
0 而成交额大于 0，或成交额为 0 而成交量大于 0，生成
`TURNOVER_INCONSISTENT` 并按同一规则降级。重复复核同一不可变 raw 证据时复用
已有的 `open/reopened` 质量问题，不重复堆积告警；若同一问题已被关闭但 raw 重检仍
失败，则新建一条 `open` 问题，不能让历史关闭状态隐藏当前仍在生效的可疑资格。

由于停牌日不进入 `market.daily_bar`，market 层不存在“停牌日成交量为零”的记录；raw 层来源若返回停牌记录，应导入 `market.security_status_event`，不在此处重复判断。

问题类型：

```text
VOLUME_INVALID
AMOUNT_INVALID
VOLUME_MISSING
AMOUNT_MISSING
TURNOVER_INCONSISTENT
```

### 4.4 交易日历

规则：

- `trade_date` 应出现在对应交易所交易日历中。
- 非交易日出现成交数据，应标记异常。
- 交易日缺少数据，需要结合 `market.security_status_event` 判断。只有该日无冲突的明确观察，或同时具有 `effective_from/effective_to` 且闭区间覆盖该日的 `suspended`、`unlisted`、`delisted` 证据才能解释无 bar；单日旧状态、开放结束日期或同日跨源冲突不得无限向后延续或静默裁决。`delisting` 证券仍可能处于可交易的退市整理期，不能仅凭状态名跳过行情补采。状态未知、上市日未知时保持“证据不足”，不得宣称缺口已补齐。
- 任何来源标记为停牌的 raw 记录不得直接进入 `market.daily_bar`，应生成 `SUSPENDED_BAR_REJECTED` 并导入 `market.security_status_event`。
- 最新 raw 修订命中停牌或其他硬拒绝时，已有非 `manual_fixed` market 行必须 fail-closed 降为 `rejected` 并退出可信读取，同时保留原值与 raw 血缘供审计；首次出现的硬拒绝只记录 raw 与质量问题，不伪造 market 行。若历史可信行已进入温层，控制面降级后还必须按 release/delta 规则发布覆盖，不能让旧 Parquet 继续被研究读取。
- 补采批次统计必须区分内容未变化、响应内完全相同的重复、同业务键但内容冲突的重复、请求区间内的非候选旁带记录、请求作用域外记录和计划内未返回。内容未变化、完全相同重复及范围内旁带记录属于幂等或范围响应跳过，不计入 `rejected_count`；同键内容冲突、错证券、错复权或越过请求日期范围计为拒绝，冲突键不任意采用第一条；计划内未返回单独记录为 `unresolved_actionable_rows`。调度失败行数还必须纳入补采后公共构建器产生的 `suspect/rejected`，不能把“来源有返回”冒充“可信缺口已修复”。

问题类型：

```text
CALENDAR_MISMATCH
MISSING_BAR
SUSPENDED_BAR_REJECTED
```

### 4.5 涨跌幅异常

涨跌幅校验需要结合证券类型、板块、ST 状态和上市日期。

首批可做保守检查：

- 单日涨跌幅绝对值超过 30% 标记为 `warning` 或 `error`。
- 新股、复牌、除权除息等特殊情况允许人工确认。

问题类型：

```text
PCT_CHANGE_ABNORMAL
```

### 4.6 多源冲突

同一 `symbol + trade_date + adjust_type` 有多个来源时，对比：

- `close`。
- `open/high/low`。
- `volume`。
- `amount`。

建议阈值：

- 价格差异大于最小价格单位或超过配置阈值，标记冲突。
- 成交量/成交额差异超过阈值，标记可疑。

问题类型：

```text
SOURCE_CONFLICT
```

当前日线执行规则：同一业务键跨源对账为真实冲突时，本轮候选不得发布；若
`market.daily_bar` 已有该键的厂商行，将其保留原值和 `selected_raw_id`，但降为
`suspect`、`is_suspect=true`、质量分 0，立即退出可信消费路径。`manual_fixed`
有独立修复审计，不由自动对账改写。相同 raw 配对重跑不重复创建
`SOURCE_CONFLICT` 问题；任一源产生新 raw 修订版本时保留新的冲突证据。
后续最新版本对账一致且自身质量校验通过，方可重新升为 `trusted`。

HiThink dump 的历史批量构建仍用批量 SQL，但凡存在任一第二来源、量额缺失或一零一正、
停牌/非法 OHLC/负量额等硬拒绝、价格连续性或极端涨幅警告、以及 raw 采集时点早于其
交易日 15:30 上海时间，发布前都调用公共质量与多源对账。已记录冲突或硬拒绝的键不会
由批量 SQL 直接重新标为 `trusted`。`source_count` 的口径是
`max(1, 该键最新且实际参与结算后对账的唯一来源名称数)`；`1` 是 schema 保底，
仅有盘中观察时不表示已有一个结算后来源。它不统计同源历史版本，也不把盘中观察伪装成
第二意见，且仍不等于独立厂商数；判断独立性须看数据源注册表和来源血缘。

若冲突行已进入温层 Parquet，控制面降级后还须基于已验证父 release 发布
`market.daily_bar` 的 delta release；只改热库不会改变已导出的研究输入。
2026-11-11 对 `399002.SZ`（2026-11-10）执行单标的重对账后，热库保留
`selected_raw_id=11284273` 但状态降为 `suspect`；发布
`20261111-194616-source-conflict` 日线 delta release 后，温层研究读取该键返回
0 条可信记录。原始值与旧 release 保留供历史复现。

### 4.7 复权口径

复权类型必须明确，不允许把不复权、前复权、后复权数据混入同一唯一键。

问题类型：

```text
ADJUSTMENT_MISMATCH
```

## 5. system.data_quality_issue

质量问题表建议字段：

| 字段 | 说明 |
|---|---|
| `dataset` | 数据集，例如 `daily_bar` |
| `symbol` | 证券代码，可空 |
| `trade_date` | 交易日期，可空 |
| `adjust_type` | 复权类型，可空 |
| `source_name` | 来源名称，可空 |
| `raw_id` | 原始记录 ID，可空 |
| `issue_type` | 问题类型 |
| `severity` | 问题等级 |
| `description` | 问题描述 |
| `expected_value` | 期望值 JSON |
| `actual_value` | 实际值 JSON |
| `status` | `open` / `ignored` / `fixed` / `false_positive` |
| `resolved_by` | 处理人 |
| `resolved_at` | 处理时间 |
| `created_at` | 创建时间 |

## 6. 自动修复边界

### 6.1 可以自动修复

可自动修复的场景：

- 日期格式转换。
- 证券代码标准化。
- 百分号转换为百分比数值。
- 千分位逗号去除。
- 字符串空白清理。
- 明确的单位转换。

### 6.2 不应自动修复

不应自动修复的场景：

- 多源价格冲突。
- 复权口径不一致。
- 成交量或成交额巨大差异。
- OHLC 关系明显错误但无法判断正确值。
- 交易日历和实际数据冲突且可能涉及停牌/复牌。

这些情况必须生成问题，等待人工确认或更高优先级来源补充。

## 7. 人工修复流程

```text
发现问题
  ↓
生成 system.data_quality_issue
  ↓
查看 raw 来源和 source_compare
  ↓
人工选择可信值或输入修正值
  ↓
写入 market.manual_correction
  ↓
更新 market.daily_bar
  ↓
关闭或标记 data_quality_issue
```

人工修复记录至少包含：

- `dataset`。
- `symbol`。
- `trade_date`。
- `adjust_type`。
- `field_name`。
- `old_value`。
- `new_value`。
- `reason`。
- `corrected_by`。
- `created_at`。
- `applied_at`。

`manual_fixed` 是自动导入和自动 market 构建的保护边界。后续 API、CSV 或 dump 批次可以继续把新候选写入 `raw` 并形成审计记录，但不得通过 upsert 静默覆盖已有 `manual_fixed` 行。若来源修订表明人工值需要再次调整，必须重新走人工修复流程并留下新的 correction 记录。

## 8. 回滚和版本

V1 可以先采用“当前表 + 修复审计”的轻量方案：

- `market.daily_bar` 保存当前可信值。
- `market.manual_correction` 保存修复历史。
- `raw.daily_bar` 保存原始来源。

后续如果需要严格版本化，再增加：

- `market.daily_bar_version`。
- `valid_from` / `valid_to`。
- 版本号。

## 9. 质量报告

每次导入或校验任务完成后，应能按以下维度查看问题：

- 批次。
- 来源。
- 证券。
- 日期范围。
- 问题类型。
- 严重等级。
- 处理状态。

这可以先通过查询 `system.data_quality_issue` 实现，后续再做 API 和可视化。

## 10. 场景示例

### CSV 与 API 收盘价冲突

1. CSV 和 API 分别进入 `raw.daily_bar`。
2. 对账发现同一 `symbol + trade_date + adjust_type` 的 `close` 不同。
3. 写入 `market.daily_bar_source_compare`。
4. 创建 `SOURCE_CONFLICT`，severity 为 `error`。
5. 不自动覆盖已有 trusted market 数据。
6. 人工确认后写入 `market.manual_correction`。
7. 更新 `market.daily_bar.quality_status = 'manual_fixed'`。
8. 回测读取修复后的 `market.daily_bar`。
