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

如果成交量为 0 但价格变化明显，应标记为可疑。

问题类型：

```text
VOLUME_INVALID
AMOUNT_INVALID
```

### 4.4 交易日历

规则：

- `trade_date` 应出现在对应交易所交易日历中。
- 非交易日出现成交数据，应标记异常。
- 交易日缺少数据，需要结合停牌状态判断。

问题类型：

```text
CALENDAR_MISMATCH
MISSING_BAR
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
