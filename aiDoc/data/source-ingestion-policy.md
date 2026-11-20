# 数据源导入策略

当前研究补采采用显式 `current_research_catchup` 模式：只针对按上海三所日历确认的最新完整交易日，使用供应商按 `TRADE_DATE` 标注的日期接口，不将无日期 clist 的当前估值与昨日收盘拼接。逐页 raw 留痕后，必须重新验证证券归属、同日可信收盘价格、指标与分所覆盖，才能发布带 `_research_batch_id` 的隔离研究观察；失败保留证据和阻断原因。报告保存 `price_as_of`（价格日期）、`financial_as_of`（财务研究日期）、`analysis_at`（真实分析截止时刻）和 `observed_at`（真实采集时刻），均不得回拨；财务公告、采集和修订版本必须在实际分析时刻前可得。准备任务的复用须同时匹配观察模式与日期，不能借用同日晚间准备成功状态绕过补采校验。

此模式明确标注“当前补采研究观察，非当晚历史名单”，固定 `pit_verified=false`，不进入通用估值/ROE因子或历史回测。原 `same_evening` 的同日晚间采集约束保持；`collect_historical_valuations.py` 仍只写 raw，历史归档成功不自动触发当前补采发布。补采只完善输入，不放宽原有鲜度、基础数据、走势与盈利现金流研究门槛。

双低一高补数入口（2026-10-28）：`python -m scripts.ingest.prepare_quality_value_financials --all-market --workers 4 --skip-complete --output outputs/quality_value_financial_prep.json`。网络抓取最多8路并行，数据库只在持共享写锁的主线程中串行复用 `HithinkFinancialCollectionService`，保留raw、批次和财报修订。年度取4期、季度取7期；每阶段独立核对具体期末和三表覆盖，源未返回最近期则记incomplete，不能因请求成功宣称补齐。不可用季度指标接口与报表补采分离，不填零或用前期指标代替。证据齐备后用 `scripts.research.screen_quality_value_database` 只读导出覆盖及名单；raw历史估值仍只留证据，不自动转可信或认证PIT。

长时间补采依赖 `duckdb_write_lock` 的OS guard互斥：活跃写进程不因锁文件超过30分钟而失去所有权；进程崩溃后OS释放guard，遗留诊断可安全恢复。`.guard` 文件不可删除，不能混跑仍按文件mtime抢锁的旧版维护脚本；各维护进程须从同一更新后的checkout启动。

进度报告在Windows被读者短暂占用时有界重试原子替换；中断后在相同日期、范围、输出路径加 `--resume` 续跑未尝试证券并保留原阶段状态。重跑失败/不完整证券可另用 `--skip-complete` 不加resume，不能把未重试的失败状态改成成功。通用现金流比不向已识别金融行业映射，金融模型继续阻断。

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

聊天助手通过已有 a-stock-data 适配器采集单只已登记的人民币 A 股腾讯报价。预检校验证券和本机 `a-stock-data-main/SKILL.md`，实际请求显式携带交易所。每次确认执行生成独立 `raw.import_batch`，以 `tencent_quote_capture` 保存解析后的原始响应到 `raw.vendor_snapshot`。按来源、采集日和内容哈希复用同一快照，本次批次仍保留实际采集时间和原快照引用。批次、快照和终态任务同事务提交，恢复只核对原引用。采集时间与供应商报价时点分别对待，当前适配器未提供可核实的报价时点。陈旧或无效快照留原始证据并标部分完成，空响应或请求失败记失败。此类快照用于查看来源，可信日线、估值发布和因子仍走各自准入流程。

通达信对账工具按完整证券代码和交易日只读已有未复权 `market.daily_bar_source_compare`。空样本表示无对账记录。工具状态和调用路径见[智能体接口说明](../../README.md#智能体会话与可审阅计划)。

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

另类数据的当前采集先提交批次，再逐响应保存 raw，最后以独立事务发布 market。后续抓取、日期解析或标准化失败时，已收到的响应保留，market 回滚、批次标失败；非法业务日期仅以真实采集日归档 raw 并注明 `market_published=false`。同来源、dataset、快照日期和内容哈希复用原 raw ID，真实变化新增版本；采集时间保存在批次，不为内容强加变化的时间字段。

此路径批次 `row_count` 为收到的响应数，`accepted_count` 为本次新增 raw 数，复用计数及原 raw/batch 引用进入 `request_params.raw_snapshot_refs`；不是 market 质量通过数。空 dataset 仍为 partial。人工重试保留历史失败，并产生新的业务任务。历史 DuckDB 重复键/索引 FATAL 未重现，不把此次证据留存改进表述为其底层原因已经消除。

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

HiThink API 日线默认回看 14 日。重复同步时，按来源、证券、交易日、复权类型、
证券类型及 `row_hash` 识别已保存的同内容版本，整批预读当前窗口的内容索引，
只追加真正新到或内容变化的原始版本；旧行没有 `row_hash` 时才以定点 OHLC、
成交量和成交额比较。跳过的相同行数记入导入批次的
`request_params.skipped_unchanged_count`，批次 `row_count` 仍表示本轮观测数量。
HiThink 公司行为 dump 同样按来源、证券、除权日及内容哈希跳过重复行，
原始值发生变化时保留新版本。`raw.corporate_action` 保留全部内容版本；
`market.corporate_action` 是每个来源、证券、除权日的最新投影，修订会整体替换该日
旧投影，包括 `cash_dividend -> mixed` 等 action type 变化，避免调整因子同时消费新旧事件。
此幂等规则不合并跨来源冲突，也不删除历史批次证据。

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

通达信独立盘后包的尝试须在发起网络请求前创建并持久化
`raw.import_batch`。下载超时、包不可用、解析后没有可比日线，均记失败批次及
请求交易日，不得记为成功或凭空生成 0 行可信数据。核心金额和价格从源值直接
转换为 `Decimal`，不增加一次浮点往返。该源当前提供独立**对账**，不作为
HiThink 失败时自动替代的权威日线；两源的指数成交量单位和历史覆盖仍不同。

## 7. QMT / MiniQMT 导入策略

后续接入 QMT 时必须明确：

- 本地数据路径或读取方式。
- 数据下载时间。
- 复权口径。
- 是否包含停牌日；若包含，停牌记录必须能识别并导入 `market.security_status_event`，不得直接进入 `market.daily_bar`。
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

- 公司行为调整包的所有行先保留在 `raw.corporate_action`；现金、送股、配股比例全部为零的行不发布到 `market.corporate_action`，不能靠默认分支误标为配股。既有误标行须单独审计，不在导入时静默删除。现有包只含除权日、金额和比例，不含登记日、支付日与源事件 ID，不能据此解锁回测的事件会计拒绝闸门。

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
- 证券和交易日历参考数据先按页保存供应商逐行 payload 到 `raw.vendor_snapshot`，再刷新 `market.security` 与交易日历；当前适配器未保留证券接口响应的原文字节。HiThink 上市日期偶发整页缺失，最多用小页重复观测 5 轮合并已有日期；同次观测或已有库值与新日期冲突时失败，禁止静默覆盖。若仍有缺失，记录本次覆盖并保留库中已核验的日期，缺失证券在研究筛选中逐股阻断；不以第一根日线推测上市日期，也不因此中断其他日线同步。证券写入只更新确实变化的行，并将 ID 序列水位推进到现有最大值之后。原始批次成功仅证明本次入库及校验完成。
- HiThink `/calendar/trading-days` 为无分页的开市日完整列表（SDK 声明约一年），不是逐自然日的开闭市枚举。仅在最新成功 reference 批次中，确认原始列表完整、行数与摘要一致、三所映射的开市日集合一致后，可在该列表最小至最大开市日的闭区间内，将自然日补集发布为派生 `closed`。必须保存源批次、原始内容哈希、推导规则版本及真实派生时间，注明三所记录映射自同一供应商 A 股日历，不构成三个独立来源的交叉验证。已有开闭市记录与本次结果冲突须阻断，不得静默覆盖；失败、残缺批次或范围外日期保持未知，不依据周末惯例、行情缺行或首尾外推猜测休市。验证应覆盖区间内节假日、区间外未知、批次失败或残缺、交易所集合冲突、已有状态冲突及重跑幂等。
- “增量”指入库按内容去重；近期包仍整包下载，以识别窗口内修订和恢复缺失可信层。历史包下载不是每次启动都会执行。供应商历史包的可用范围以实际发布为限。
- 窗口衔接按 SH/SZ/BJ 的开放交易日判断，周末和节假日不算缺口；导入前要求三个交易所的日历范围覆盖本轮发布窗口，日历缺失或陈旧时任务失败，不把未构建的窗口当作已处理。
- 原始导入遵守第 9 节内容版本规则；可信层按来源与本次窗口复用最新 raw 候选，执行价格、成交和交易日校验，保留 `manual_fixed`。即使本批新增 raw 为零，也必须可以重试可信层构建。
- 批量构建先以集合 SQL 处理完整历史；存在任一第二来源、量额缺失或一零一正、停牌/非法 OHLC/负量额等硬拒绝、价格连续性或极端涨幅警告、以及 raw 采集时点早于其交易日 15:30 上海时间的日期，在提交前再次运行通用质量构建与多源对账。警告必须生成与逐行构建相同的问题并降为 `suspect`；硬拒绝使已有非人工修复 market 行保留原值和 raw 血缘但以 `rejected` 退出可信路径。历史特殊处理状态只复制所选 raw 行携带的日期明确证据；raw 为空时保持 NULL，不得用证券主表的当前简称派生状态倒填历史。批量 SQL 不得把警告、硬拒绝或已有冲突直接恢复为 `trusted`；恢复必须由最新 raw 版本经公共规则复核通过。重复复核同一 raw 的未关闭同类问题保持幂等。
- 近期 QFQ 缺口补采先生成分页的只读计划，覆盖全部候选而非默认前 1000 行。计划区分完全缺行、量额字段不完整、其他 `suspect`、明确状态解释及上市日期未知；自动采集只处理完全缺行和量额不完整，其他可疑原因保留诊断。明确停牌只接受当天观察或有效区间证据，上市日未知不冒充确认缺口。来源只返回部分计划键或任一来源报错时批次记 `partial` 并保存未解决数；raw 内容相同不重复追加，但仍允许重试后续 market 构建。批次将内容未变化、响应内完全相同的重复及请求区间内的非候选旁带行情记为跳过；任一来源同键但内容冲突时，本批所有来源都不得解除该键，且与错证券、错复权或越界响应一并计为拒绝；计划内未返回单列为可执行但未解决。冲突审计只保存有数量上限的业务键、来源和数值摘要哈希，并显式记录截断数，不重复落完整响应。调度状态还要纳入后续公共构建器的 `suspect/rejected`，避免以 `fetched - inserted` 或“来源有返回”误报成功。
- 对已经导出到温层的日期，质量状态修订须发布新的日线 delta release，并通过温层研究读取验证；`scripts.maintenance.export_warm_layer --base-release-tag ... --include-table market.daily_bar` 可仅导出该表，父 release 必须已通过验证。旧 release 作为可复现快照保留。
- 近期逐证券同步与缺口补采共用“上海时间已完整过去的日期”截止口径，不使用主机本地 `date.today()` 把当日盘中快照当作完整日。全市场 dump 的快速路径按每条 raw 自身的 `imported_at` 与其交易日 15:30 上海时间比较并回到公共质量构建，不能只复核构建当天，否则隔日重建会重新信任历史盘中快照。不把停牌、节假日或缺失记录填为零。最新日股票横截面覆盖低于 active 且已上市股票的 95% 时，同步失败且不推进水位；指数覆盖单独报告，不混入股票分母。
- 全市场未复权日线成功推进后，自动任务继续触发 `qfq(v1)` 复权因子、`curated/prices_qfq` 与因子批量计算；若同一行情水位尚无 `publish_eligible=true` 的 `daily_loop`，即使日期没有再次前进也会重试。curated 先写临时目录，目标日股票与 7 个生产价量因子覆盖、重复键和 QFQ 完整性闸门通过后才原子切换正式目录并激活 release。零行因子族记为 `empty/partial`，不得伪装为成功；它不替代相应上游数据源，也不自动进入研究或交易。
- 来源若携带停牌、ST、*ST、退市整理等状态字段，应写入 `market.security_status_event` 并记录来源与批次；这些记录不进入 `market.daily_bar`。
- 成功任务的 `metadata.end_date` 是整体处理水位，只有参考数据、raw、market 构建和最新日股票横截面覆盖闸门全部完成才更新；它仍不代表指数或每只停牌证券都有 bar。成功水位按 `started_at`（再以 id 作同刻 tie-breaker）取最近任务，不能依赖可能在控制面重建后回退的序列 id。失败任务保留错误日志，下轮仍从上次成功水位判断覆盖。批次号、release、跳过数、可信记录数和逐所覆盖留在任务 metadata 中。
- `AUTO_DATA_SYNC_ENABLED` 控制开关，`AUTO_DATA_SYNC_INTERVAL_SECONDS` 控制间隔（最小 60 秒）；测试默认禁用外部同步。单 worker 串行运行，关闭等待正在执行的任务完成；不可同时对同一库启动独立写库调度进程。

## 13. 全市场相对估值的晚间研究观察值

- API 内任务在上海交易日 21:00 后采集当前东方财富估值分页；当前 clist 首次请求不可用时，可退回到供应商按**当天** `TRADE_DATE` 标注的估值分析分页，再经同日可信日线核价。中途分页失败不切换来源。两条路径同日晚间失败可每 30 分钟重试。仅以当日已成功的 `auto_data_sync` 水位、完整交易所日历和可信未复权日线作为发布前置条件。跨日停机不得用新抓取的当前报价补写旧日。
- 每页原始响应及 UTC 采集时间、供应商报告总量、目标交易日、原始哈希写入 `raw.vendor_snapshot`，由 `raw.import_batch` 记录来源、批次、代码版本和接受/拒绝数。分页未读完、证券重复、覆盖不足或网络中断时保留失败 raw 与原因，不发布研究观察值。
- 只有 CNY 股票且供应商市场码、沪深北证券归属一致，收盘价与当日可信未复权日线相差至多 0.01 元，才进入 `market.valuation_snapshot`；按交易所分别验收日线、供应商映射与价格匹配覆盖。行内回指原始批次与页哈希；旧同日市场行被新验证行替换前另存 raw。clist 的 f9 周期未认证；退回路径虽有供应商 `PE_TTM` 字段，页面仍只作为供应商 PE 展示；两者均不填充通用 `pe_ttm`。旧的东方财富估值直写 market 入口已禁用。
- **通用 `pe_ttm` 的另一来源（2026-11-11 修订）**：上一条的「不填充通用 `pe_ttm`」针对的是**东方财富 f9**——实测证明 f9 与腾讯 `pe_static` 在全市场 5,576 个标的中 5,575 个（99.98%）1% 以内一致，即 f9 是**静态 PE**，报告期未经认证，所以不能冒充 TTM。腾讯行情的 `pe_ttm` 字段是**真正的滚动十二个月 PE**，作为独立厂商的第二来源写入 `market.valuation_snapshot` 的通用 `pe_ttm` 列，供应商为 `source_name='tencent'`。该源另有四条硬约束（`aiDoc/relations/20261111-1531-tencent-valuation-source.md`、`aiDoc/relations/20261112-1005-tencent-quote-day-roll.md`）：
  - **两个价格字段都相对「厂商行情日」定义，行情日在 09:15（集合竞价开始）滚动，不是午夜**（2026-11-12 修订）。`price` 是行情日的价格（盘后/盘前为该日收盘的冻结值，盘中为实时 tick），`last_close` 是行情日**前一个交易日**的收盘。因此「取历史日收盘价只能用 `last_close`」是错的：目标日等于「严格早于当前北京自然日的最后交易日」时，**09:15 之前与非交易日用 `price`，09:15 之后的交易日用 `last_close`**，由 `close_field_for_quote_day()` 判定，**禁止写死**。写死的失败是静默的：2026-11-12 09:03 那次跑把 11-10 的收盘标成了 11-11（435 行里 226 行错标），其余 5,138 行被价格闸门挡掉，故障表现为「覆盖率只有 7.8%」而非「日期错了」。
  - **选中的字段必须与可信收盘实测校验**：一致率低于 50% 即中止运行并报错。厂商的行情日滚动是行为而非保证，规则一旦失配必须响亮失败，不得继续写入错日期的行。
  - **厂商的市值与 PE/PB 是按行情日价格算的**，与所选收盘放在一起前必须重述（`mcap × close / price`），否则一行之内数字互相矛盾；`limit_up`/`limit_down` 是唯一按前收盘算的例外。**判定「这行属于哪一天」的容差必须小于一个最小跳动（0.01）**，否则相邻交易日的收盘价会互相匹配。
  - **该源只能描述最近一个已完整过去的交易日**。目标日不等于「严格早于当前北京自然日的最后交易日」时直接拒绝，不返回被错标日期的行；实测收紧价格容差无法根治（低价股一个最小变动单位就是 0.44%，整周未交易的标的仍会蒙混通过）。
  - 价格闸门为**绝对 0.01 元且相对 0.2% 双阈值**，并按 `market.security` 白名单过滤代码空间碰撞（2,144 个指数代码中有 108 个与个股代码段重叠，腾讯会把指数代码当上市公司报价）。
  - 发布的行同样带 `_research_batch_id` 隔离标记，通用因子读取器不消费，直到 PIT 消费路径完成隔离。
- 同日晚间另取东方财富业绩报表 `RPT_LICO_FN_CPD`，从最近报告期向前查找两个已有披露值的报告期；季度刚结束而尚无报表时，空响应留 raw 并继续回溯。每页原始响应、哈希、抓取时间与披露日期先写 raw；逐行要求报告期早于交易日、公告日不晚于交易日、供应商披露时刻不晚于实际观察时刻，分页跨午夜即失败且不发布。`WEIGHTAVG_ROE` 从百分数换算为比率，连同报告期、公告日、原始页哈希附在同日研究估值观察值中；保留每个报告期的逐股值，选择沪深北各所对价格匹配证券均覆盖至少 80% 的最新公共期；页面阈值只作用此主期。若较旧期获选，已披露更新期的证券同时展示两期 ROE 与各自原始哈希供人工核查；主期缺失值仍逐股待复核，不用单期代理补准入，也不写通用 `roe_ttm/v1`。供应商 `BOARD_NAME` 仅作行业提示；准入只用已核证券行业，来源冲突或金融行业单独复核。
- 估值与 ROE 的 raw、当前研究观察值及成功标记按任务门槛发布；供应商报表不具备完整首次可得和修订版本证明，输出固定 `research_only=true / pit_verified=false / execution_eligible=false`。研究估值行用 `_research_batch_id` 隔离，通用 `ValuationSnapshotRepository.list_available` 与市值/估值因子不读取这些行。午夜报告还要求目标日成功准备任务及同批次原始哈希，缺输入即阻断；上市日期、ST 状态、行业或个股 ROE 未核验时保留逐股阻断，不能把未知补为正常；若全市场均被阻断，页面默认展示分页原始指标和逐股缺口，不称为通过名单。页面按沪深北显示可信日线、同价估值、有效 PE/PB、披露 ROE 及证券属性的实际覆盖，不能用该名单认证历史收益。
- 东方财富估值分析另有按 `TRADE_DATE` 查询的 `RPT_VALUEANALYSIS_DET` 历史列表，可补采供应商标注交易日的 `PE_TTM/PB_MRQ/CLOSE_PRICE` 原始资料。该路径独立于无日期参数的当前 clist；使用 `collect_historical_valuations.py --trade-date YYYY-MM-DD` 手工执行，只写 `raw.import_batch`、`raw.vendor_snapshot` 与 `system.job_log`。逐页保存原始响应 JSON 文本及 SHA256、真实 UTC 抓取时间、请求日期、供应商总数和页码，校验总页数、逐行日期、证券代码与重复行。重采建立新批次以保留修订痕迹；失败保留已抓取页及失败状态。批次成功仅表示此次供应商响应完整，不表示本地证券覆盖、历史首次可得时间或财务口径已认证。
- 历史补采不得直接写 `market.valuation_snapshot` 或因子。通用估值因子按 `trade_date` 消费非研究估值行；若把晚采历史数据当普通行发布，仍会污染历史回测。未完成历史版本、首次可得时间和消费路径隔离前，历史 raw 批次不解锁午夜初筛，不用于收益计算或交易。旧晚间任务生成的通用 ROE 因子 run 356 已归档 5,552 行并失效，不再供策略读取。
- **历史补采的发布位置（2026-10-26 修订）**：上一条禁止的是「把历史补采直接发布到 `market` 可信层」，不是禁止研究层使用历史补采。估值历史补采现在发布为**温层 Parquet 研究表** `research.valuation_history`（判定标准见 `aiDoc/modules/schema-plan.md` §17：只存在于 Parquet、无 ORM 模型、无 Alembic 迁移、不出现在控制面 `information_schema`），由 `scripts/ingest/eastmoney/collect_valuation_history.py` 执行。约束不变：全部行 `pit_verified=false`，不写 `market.*`，不进入通用估值因子；消费方必须显式选择研究路径。ORM 侧孪生读取 `app/repositories/research_valuation.py` 读控制面的 `market.valuation_snapshot`，两条路径的列名/排序/过滤语义一致，但 `pb_mrq` 在 ORM 侧报 `None`、`pit_verified` 在 ORM 侧为 `true`——两处差异都是刻意的不等价，见 `aiDoc/data/20261026-valuation-history-backfill.md` §6.1。
- **历史补采的供应商分页必须用复合排序**：东财 `RPT_VALUEANALYSIS_DET` 的分页请求须带 `sortColumns=TRADE_DATE,SECURITY_CODE` 与 `sortTypes=1,1`。只按 `TRADE_DATE` 排序时服务端对同一交易日内部的行序不作保证，实测 8 个交易日的分页在第 1、2 页之间重复 571 个 `(symbol, trade_date)` 键并同量丢行；这种缺口会以「覆盖率不足」的形式伪装成供应商数据缺失，必须在采集侧挡住。
- **历史补采的原始响应体不写控制面**：`raw.vendor_snapshot` 只落页级元数据（请求参数、响应 SHA256、行数、响应字节数、抓取时刻），明细留在温层 Parquet。完整响应体约 1.6 MB/页，一次中断的采集即可让控制面增长约 1 GB；`scripts/maintenance/shrink_control_plane.py` 可剥离早期版本遗留的响应体而保留 SHA256 与审计能力。

当前研究的财务诊断读取数据库 JSON 时须保留数值文本，交由 `parse_float=Decimal` 解析；不得先经 ORM JSON 解码为 Python float 再转换。此约束保留原有浮点拒绝规则，避免精确金额被误判为缺证据，也避免用已损失精度的金额参与计算。

## 14. 公司行为登记日与派息日补证据

现有同花顺 A 股 `adjustment-factors` 接口和全市场调整包已经提供除权除息日 `ex_date_ms`、每股现金分红 `dividend_per_share`、送股比例 `per_share_bonus`，并已进入 `raw.corporate_action` / `market.corporate_action`。2026-10-28 对 `600519.SH` 的 REST 实测返回 30 条事件，字段并集仅有 `ticker/ex_date_ms/dividend_per_share/per_share_bonus`；本地调整包列也没有登记日或派息日。因此这里补的是**同一 A 股事件**的股权登记日与实际派息日，不重复计算已有的除权除息日期和分红金额。同花顺的基金分红接口虽另有登记/派息日，但基金事件不能代替 A 股公司行为。

- `CORPORATE_ACTION_EVIDENCE_SYNC_ENABLED` 开启后，`AutoDataSyncWorker` 每轮对缺登记日或派息日的证券按 Tushare `dividend` → AKShare 巨潮 `stock_dividend_cninfo` → AKShare 包装的东方财富 `stock_fhps_detail_em` 尝试。Tushare 每轮最多 1 只且最近一次尝试未过 65 分钟时跳过；巨潮每轮默认最多 10 只，东财仅在前两者缺完整日期时请求。主源报错、空结果或**当前待补除权日**没有有效登记/除权/派息日期时切换；该证券旧事件的完整行不能算当前事件已补齐。东财接口没有派息日，最多标记 `partial`，不能算完整公司行为。手工入口 `python -m scripts.ingest.collect_corporate_action_evidence --source auto` 共用服务层逻辑；维护脚本写主库前持有 DuckDB 写锁。
- 每次请求先建 `raw.import_batch`，记录证券、接口、UTC 抓取时间和 `raw_only/pit_verified=false`；供应商完整行数组保存在 `raw.vendor_snapshot`，按来源、证券、接口和响应内容计算 SHA256。内容不变只记新批次，内容修订追加新快照；空响应和失败均留批次。令牌只在 `.env`/settings 中，绝不写请求参数、原始 payload 或日志。
- 原始记录尚未按公告日、实施进度、除权日、每股/每 10 股分红和送转口径逐事件配对，也未完成跨源冲突核验，因此不得自动写 `market.corporate_action`、不得把晚采历史记录标成当时已知、不得解除价格价值回测的公司行为事件会计拒绝闸门。历史 ST/行业与退市宇宙缺口仍独立存在。

## 15. 换手率因子输入的分阶段验收

- `Tushare daily_basic` 可以按交易日获取全市场 `turnover_rate`，口径为流通股本换手率的**百分数值**；`turnover_rate_f` 是自由流通股本换手率，二者不得互换。AKShare `stock_zh_a_hist(adjust="")` 可按证券获取未复权行情与换手率，亦为百分数值。新入口 `python -m scripts.ingest.collect_turnover_evidence --source tushare --trade-date YYYY-MM-DD` 或 `--source akshare --trade-date YYYY-MM-DD --symbol 600519.SH` 先写 `raw.import_batch/raw.vendor_snapshot`，包含接口、参数、UTC 观察时刻、完整返回行、响应哈希与同日可信未复权收盘价对账统计。
- 同一证券/日期重复、缺换手率、非有限或超出 0%~100%、供应商收盘价与可信收盘价相差超过 0.01 元，分别计入异常；缺可信 bar 单独统计。返回行和验收报告均保留为 `raw_only/pit_verified=false`。晚采历史数据缺当时快照与修订链，不能把对账通过等同于 PIT 通过。
- 当前 `market.daily_bar` 仍无字段级换手率来源引用；整行 raw 发布器会一起替换可信 OHLCV。因此本阶段不把供应商换手率发布到 market/curated，也不生成 `turnover_rate`、`turnover_percentile_120` 或依赖它们的拥挤度生产因子。后续需先补字段级血缘、历史 120 个交易观测与两条 curated 构建路径，再进行发布评审。
- `--source auto --trade-date YYYY-MM-DD --symbol 600519.SH` 对同一证券先尝试 Tushare 全市场 `daily_basic`，再尝试东财（经 AKShare）逐股 `stock_zh_a_hist`。仅当有有效换手率且未复权收盘价与可信 bar 相差不超过 0.01 元时停止切换。`TURNOVER_EVIDENCE_SYNC_ENABLED` 开启后，每轮对目标日可信未复权 bar 中缺换手率的证券轮转补采，默认最多 20 只；Tushare 全局最近一次请求未过 65 分钟时跳过，单轮最多请求一次，余下逐股走东财。结果仍仅是 raw 证据。

## 16. 退市信息的分字段兜底

- `python -m scripts.ingest.collect_delist_evidence --source auto --exchange SZ` 或 `SH` 优先尝试 Tushare `stock_basic(list_status=D)`；失败或该交易所缺有效退市日期时，改采 AKShare 包装的交易所名单。深交所 `stock_info_sz_delist(symbol="终止上市公司")` 的 `终止上市日期` 可以作为退市日期候选。上交所 `stock_info_sh_delist(symbol="全部")` 的 `暂停上市日期` 仅是暂停上市日期，不能用来填 `delist_date`；此时返回 `partial`，不宣称退市信息补齐。
- `DELIST_EVIDENCE_SYNC_ENABLED` 开启后，每 24 小时最多运行一次；Tushare 全市场名单只请求一次，再按 SH/SZ 判断是否需要交易所兜底。每个请求均保存独立 `raw.import_batch`、原始快照、抓取时刻、哈希、失败原因及字段语义。`market.security` 尚未建立字段级来源/历史有效期校验，故不自动发布，亦不解除退市样本偏差闸门。
