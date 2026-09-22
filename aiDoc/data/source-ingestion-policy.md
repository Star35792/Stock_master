# 数据源导入策略

本文定义 Stock Master V1 如何接收外部数据。核心原则：任何数据源默认不可信，所有来源必须先进入 `raw` 层，再经过清洗、校验、对账和修复后，才能进入 `market` 可信层。

## 1. 支持来源分类

V1 和后续阶段可能接入：

| source_type | 示例 | 说明 |
|---|---|---|
| `csv` | 本地 CSV / Excel 导出 | 首批优先支持，适合手工导入和券商软件导出 |
| `api` | 东方财富、新浪、腾讯等接口 | 稳定性取决于接口变化，必须记录请求参数 |
| `akshare` | AKShare 包装的数据接口 | 作为一种 API 来源，不作为绝对真源 |
| `qmt` | QMT / MiniQMT 本地数据 | 后续接入，需明确复权和本地缓存口径 |
| `manual` | 人工补录 | 只用于少量修复，必须审计 |
| `vendor` | 付费源 | 仍需校验和血缘追踪 |

## 2. 总原则

1. 外部数据不得直接写入 `market.daily_bar`。
2. 每次导入必须生成导入批次。
3. 每条原始记录必须记录来源名称、来源类型、批次 ID 和导入时间。
4. 原始数据尽量完整保存到 `raw_payload`。
5. 来源优先级只能作为候选选择因素，不能替代质量校验。
6. 导入失败、部分成功、重复导入都必须可追踪。

## 3. 导入批次

每次导入都应创建 `raw.import_batch`。

建议字段：

| 字段 | 说明 |
|---|---|
| `batch_no` | 批次号，唯一 |
| `source_name` | 来源名称，例如 `csv`、`akshare`、`qmt` |
| `source_type` | 来源类型 |
| `dataset` | 数据集，例如 `daily_bar` |
| `file_path` | 文件来源路径，可空 |
| `request_params` | API 请求参数 JSON，可空 |
| `started_at` / `finished_at` | 开始和结束时间 |
| `status` | `running` / `success` / `failed` / `partial` |
| `row_count` | 原始行数 |
| `accepted_count` | 接收行数 |
| `rejected_count` | 拒绝行数 |
| `error_message` | 错误信息 |

批次用于回答：

- 数据什么时候来的？
- 从哪里来的？
- 请求或文件是什么？
- 成功多少行，失败多少行？
- 后续 market 记录能追溯到哪个导入任务？

## 4. raw.daily_bar 原始记录

`raw.daily_bar` 保存所有来源的日线记录。

建议字段：

| 字段 | 说明 |
|---|---|
| `batch_id` | 关联导入批次 |
| `source_name` | 来源名称 |
| `source_type` | 来源类型 |
| `source_priority` | 默认来源优先级，越小优先级越高 |
| `symbol` | 标准化后的证券代码，尽量在导入阶段生成 |
| `raw_symbol` | 来源原始代码 |
| `security_type` | 证券类型 |
| `trade_date` | 交易日期 |
| `adjust_type` | 复权类型 |
| `open` / `high` / `low` / `close` | 来源解析后的 OHLC |
| `pre_close` | 前收盘 |
| `change_amount` / `pct_change` | 涨跌额、涨跌幅 |
| `volume` / `amount` | 成交量、成交额，导入阶段可先记录解析值 |
| `turnover_rate` / `amplitude` | 换手率、振幅 |
| `raw_payload` | 原始完整字段 JSON |
| `row_hash` | 行内容 hash，用于识别重复 |
| `imported_at` | 导入时间 |

`raw` 层允许存在重复和冲突，但必须可识别。

## 5. CSV 导入策略

CSV 是 V1 优先支持的数据入口。

### 5.1 文件要求

建议标准 CSV 字段：

```text
symbol,trade_date,open,high,low,close,pre_close,volume,amount,adjust_type
```

可选字段：

```text
change_amount,pct_change,turnover_rate,amplitude,security_type,source_name
```

### 5.2 日期格式

允许解析：

- `YYYY-MM-DD`
- `YYYYMMDD`

进入数据库统一为 `date`。

### 5.3 代码标准化

导入时应把来源代码转换为标准 `symbol`：

| 原始代码 | 标准 symbol |
|---|---|
| `600519` | `600519.SH` |
| `000001` | `000001.SZ` |
| `159915` | `159915.SZ` |

无法判断交易所时，记录质量问题，不应直接进入 trusted market。

### 5.4 编码

优先支持：

- UTF-8
- UTF-8 with BOM
- GBK / GB18030（券商软件导出常见）

导入批次应记录实际识别或指定的编码。

### 5.5 重复行

同一 CSV 内出现重复 `symbol + trade_date + adjust_type`：

- 若内容完全一致，记录为重复但可保留一条候选。
- 若内容不同，生成 `DUPLICATE_BAR` 或 `SOURCE_CONFLICT` 问题。

## 6. API / AKShare 导入策略

API 类来源必须记录：

- 请求 URL 或接口名称。
- 请求参数。
- 时间范围。
- 复权类型。
- 返回行数。
- 错误和重试次数。

AKShare 只是一种 API 包装来源，不应在架构上享有特殊信任。

API 失败处理：

- 网络错误：批次状态为 `failed` 或 `partial`。
- 部分证券失败：记录失败列表到 `error_message` 或 `metadata`。
- 字段缺失：原始记录可进入 raw，但必须生成质量问题。

## 7. QMT / MiniQMT 导入策略

后续接入 QMT 时必须明确：

- 本地数据路径或读取方式。
- 数据下载时间。
- 复权口径。
- 是否包含停牌日。
- 成交量和成交额单位。
- 证券代码到标准 `symbol` 的映射规则。

QMT 数据同样先进 `raw`，不直接覆盖 `market`。

## 8. 手工补录策略

手工补录只用于少量异常修复，不作为大规模数据入口。

要求：

- 必须记录处理人。
- 必须记录原因。
- 必须记录修复前后值。
- 必须关联数据质量问题或原始记录。

## 9. 幂等与重复导入

导入系统需要支持重复执行。

建议做法：

- 批次号唯一。
- 每条 raw 记录计算 `row_hash`。
- `source_name + symbol + trade_date + adjust_type + row_hash` 可用于识别内容重复。
- 同一来源重新导入同一时间段，不应无声覆盖已有可信数据。

HiThink Parquet dump 的批量导入进一步约定：

- 先把价格、成交量和成交额转换为 `raw.daily_bar` 的目标 `DECIMAL` 精度，再判断内容是否相同。
- 同一 `source_name + symbol + trade_date + adjust_type` 下，`security_type`、OHLC、`volume`、`amount` 完全相同的记录不重复写入；历史快速导入留下的空 `row_hash` 也按这些业务字段兼容识别。
- 新写入记录保存供应商完整行的 `raw_payload`，并对包含代码、币种、周期、复权标记、时间、OHLCV 和成交额的 payload 计算 `row_hash`。
- 同一业务键任一内容发生变化时，必须新增 raw 版本，不能覆盖旧版本；同一 release 内同键冲突则整批失败，避免任意选择。
- dump 批次的 `row_count` 表示输入行数，`accepted_count` 表示新写入或修订版本数，完全重复数写入 `request_params.skipped_count`；即使新增数为 0，批次仍保留本次检查记录。
- market 回填不局限于本批新增 raw；缺失可信记录时可以复用既有同来源 raw 候选，但任何自动构建都不得覆盖 `manual_fixed`。

## 10. 来源优先级

可以维护默认来源优先级，例如：

```text
manual < vendor < qmt < csv < api < akshare
```

数值上可用越小优先级越高。

但来源优先级不能单独决定最终结果。最终进入 `market` 应综合：

- 字段完整性。
- 价格逻辑。
- 交易日历。
- 复权口径。
- 多源一致性。
- 历史稳定性。
- 人工确认。

## 11. 数据血缘

从 `market.daily_bar` 应能追溯到：

- 采用的 `raw.daily_bar`。
- 参与对账的其他 raw 记录。
- 导入批次。
- 来源名称和类型。
- 质量问题。
- 人工修复记录。

这是后续排查回测异常和数据错误的基础。

## 12. 运行期间自动增量补齐

- API 启动完成后立即后台运行一次 `auto_data_sync`，每次结束后等待配置间隔再运行（默认一小时）。默认启用，需配置 HiThink API key；缺 key 时仅警告，网络或导入失败不阻止 API 提供已有数据。
- 范围为供应商全市场 `none` 未复权日线。每轮刷新证券与交易日历，下载近期包并读取实际起止日。首次没有成功的自动同步基线时先处理历史全量包，再处理近期包；之后若上次成功水位与近期窗口之间有遗漏交易日则再次用历史包回补。历史包自身覆盖不足时任务失败，不宣称补齐。
- “增量”指入库按内容去重；近期包仍整包下载，以识别窗口内修订和恢复缺失可信层。历史包下载不是每次启动都会执行。供应商历史包的可用范围以实际发布为限。
- 窗口衔接按 SH/SZ/BJ 的开放交易日判断，周末和节假日不算缺口；导入前要求三个交易所的日历范围覆盖本轮发布窗口，日历缺失或陈旧时任务失败，不把未构建的窗口当作已处理。
- 原始导入遵守第 9 节内容版本规则；可信层按来源与本次窗口复用最新 raw 候选，执行价格、成交和交易日校验，保留 `manual_fixed`。即使本批新增 raw 为零，也必须可以重试可信层构建。
- 上海时间 18:00 前，只向 market 发布前一自然日及以前的数据；18:00 后允许当天，仍受供应商实际发布与交易日历限制。不把停牌、节假日或缺失记录填为零，不自动重建 qfq/hfq、复权因子或因子结果。
- 成功任务的 `metadata.end_date` 是整体处理水位，只有参考数据、raw 和 market 构建流程全部完成才更新；不是每只证券数据完整或通过质量校验的保证。失败任务保留错误日志，下轮仍从上次成功水位判断覆盖。批次号、release、跳过数和可信记录数留在任务 metadata 中。
- `AUTO_DATA_SYNC_ENABLED` 控制开关，`AUTO_DATA_SYNC_INTERVAL_SECONDS` 控制间隔（最小 60 秒）；测试默认禁用外部同步。单 worker 串行运行，关闭等待正在执行的任务完成；不可同时对同一库启动独立写库调度进程。
