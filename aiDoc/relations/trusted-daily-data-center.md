# V1 可信日线数据中心架构

2026-10-28 边界补充：研究回测详情、利润状态、基准与失败重跑计划显式从独立研究库读产物，行情仍取主库；测试以无 strategy 表的主库验证该边界。研究库启动初始化也协调研究写锁。写锁使用常驻 `.guard` 文件的 OS 互斥，不按年龄抢占活跃任务，崩溃后由 OS 释放；不要删除 `.guard`。估值 ORM 研究读取排除 `_research_batch_id` 观察，默认 `pit_verified=false`，与温层一起保留来源，不因进入可信库而认证历史首次可得性。

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
- 已有的 `factor.*`
- 已有的 `strategy.*`（产物位于独立研究库）

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

### 6.5 采集时点准入：盘中快照不得标 trusted（2026-11-11 新增）

`trusted` 的含义是「这根 bar 是一个完整交易日的终值」，不是「数值看起来合理」。因此质量服务除数值校验外，还要校验**采集时点**：

- 判据是 `raw.daily_bar.imported_at`，不是 market build 的运行时刻。
- 若 `imported_at` 早于该 bar 自身交易日的 **15:30 Asia/Shanghai**（15:00 收盘 + 定稿缓冲），该 bar 记为 `suspect` 并附 `INTRADAY_SNAPSHOT` 问题行，同一交易日另记一行 `INTRADAY_SNAPSHOT_BATCH` 给出处置动作。
- 与该 bar 自己的交易日比较，而不是与「今天几点」比较：次日早上补抓前一日完整时段是合法回填。
- 缺失 `imported_at` 不降级——查不到采集时间不等于盘中采集。

理由与完整推演见 `20261111-0300-intraday-snapshot-guard.md`。一句话：盘中快照数值自洽，只有出处能证伪它；而一旦以 `trusted` 进入估值锚或因子截面，错误不会报错，只会静静算出一个自信的结论。

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

- 主 DuckDB（`data/stock_master.duckdb`）经 SQLAlchemy ORM 承载 `raw -> market -> factor`、`portfolio/risk -> trade` 与控制面（物理表如 `metadata.job_log`，历史逻辑域称 `system`）。`strategy` 回测与训练产物存独立 `data/research.duckdb`，手工真实持仓账本存 SQLite `data/positions.db`；估值仍消费主库可信行情。`daily_loop` 编排器实际读写的是这一路径。可信性仍由数据契约、质量校验和血缘语义定义，不因换库而放宽。
- `backend/app/lake` 是 Parquet/DuckDB 温层工具（`curated/prices_qfq`、`fundamentals_pit`、`factor` release）。`curated/prices_qfq` 已接入 `daily_loop`：置真 `factor_reads_curated` 且 `adjust_type='qfq'` 时，先从 warm history + control hot window 构建临时数据集，因子通过绑定该临时路径的 `CuratedDailyBarReader` 计算。发布闸门通过后先切换正式目录并暂留旧树，两条 release 激活事务提交成功后才回收旧树；激活或提交失败则回滚数据库并恢复旧树，新树退回暂存供诊断。关闭开关时仍走 ORM。其余 curated 数据集仍由离线脚本驱动。
- 容量边界：本机只保留各职责的必要事实，不自动复制整库。`curated/prices_qfq` 的旧物化目录仅在发布切换期间临时存在，发布失败恢复旧目录，发布成功删除本次明确替换的旧目录。普通构建不按名称或年龄自动清理历史暂存、替换或备份目录；清理前必须核验活动任务、当前读路径、manifest/行数和研究审计引用。温层 `exported` 的有效父链、增量层和被研究/审计引用的 release 必须保留，不能按时间直接清空。
- 持续写入边界：HiThink API 的回看窗口和公司行为 dump 重跑按同源业务键与内容版本幂等写入 `raw`，旧值修订仍新增版本；可信日线只有字段或选中来源变化时才更新。离线 Parquet 分区压缩经行数核验后切换，旧文件只在成功发布后回收。主库必要的新交易日与有效温层 delta 仍会随事实增长，不能用按年龄删 raw 或有效 release 伪造容量稳定。
- `export_warm_layer.py` 逐表校验失败即停，只在所有选定表通过后原子写可消费的 release summary；失败时尝试删除本次未发布目录，重用已存在 tag 会直接拒绝，不覆盖既有事实。硬中断或删除受阻留下的无效目录需后续核对清理；旧版本清理仍需检查 active、父链及研究引用。
- 东财研究估值历史采集同样拒绝覆盖已发布的 release tag，发布前检查成功父链和深度。当前研究温层是估值历史明细的权威数据；该流程的 `raw.vendor_snapshot` 仅留页哈希、行数及抓取时间，不足以从 raw 重建已删除的 Parquet 明细。
- 估值筛选全量 JSON 只供最新工作台与当前跟踪使用；每次发布保留最新报告和冻结 cohort，旧任务日志保留摘要及快照哈希，清除旧文件引用后回收旧 JSON。历史全量报告无法精确回看是用户接受的容量取舍；原始供应商证据仍在 `raw`，此清理不改变 PIT/质量状态。
- `libs/hithink_sdk/marketdb` 是独立 DuckDB SDK/toolkit，适合 notebook、离线研究和 HiThink dump 使用，不直接等同于后端 `market` 可信层。

分层存储最初决策见 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`；当前还包含 [SQLite 账本例外](20261023-1925-position-ledger-sqlite.md) 与 [独立研究库](20261024-1427-research-database-split.md)，不再以早期“纯单库、无 SQLite”描述全部存储。`curated/prices_qfq` 已接入 `daily_loop`（`factor_reads_curated` 开关），`fundamentals_pit`、factor release 与完整 Parquet release 发布/校验仍属后续切片。

### 10.1 研究读路径（2026-10-22 新增）

回测与因子研究的**读**路径已经可以直接读温层 Parquet，**写路径仍然只走 ORM**，控制面不因研究读而变大。

- `app/repositories/research_read.py` 的 `WarmResearchReadRepository` 是 ORM 仓库的只读孪生实现，暴露 `list_daily_bars` / `list_factor_values` / `load_execution_calendar` / `coverage` / `total_trading_days` / `max_exported_date` / `tradability_coverage`。日线读取时会用 `app/market/price_limit.py` 补齐 `limit_up` / `limit_down`（对缺少供应商限价的记录推导，不据此声称历史状态证据完整）。
- `app/services/backtest.py` 与 `app/services/strategy_research.py` 通过 `_research_read()` / `_factor_read()` 上下文管理器切换数据源，`research_read_source=warm|control`。**单元测试固定用 `control`**（`tests/conftest.py` 设置 `RESEARCH_READ_SOURCE=control`），否则临时测试库无法自己 ATTACH 自己。
- 两道 fail-closed 闸门：`research_min_trading_days`（默认 200，release 被截断时直接失败）与「窗口末日不得超过 `max_exported_date`」。不要为了让回测跑起来而调低它们。
- 温层连接默认 `attach_control=False`，即纯 Parquet 连接，不 ATTACH 控制面文件——DuckDB 不允许同一文件被两个句柄打开（其中一个为读写）时报 `Unique file handle conflict`。`market.corporate_action` 与 `market.trading_calendar` 因此仍走 ORM（这两张表未被瘦身，持有完整历史）。
- 子 release 只发布变更表，其余表通过 `WarmLayerStore.base_release_tag` 从父 release 继承，避免每次复制约 700 MB Parquet。
- release 中每张表显式标记 `snapshot` 或 `delta`：snapshot 完整覆盖祖先同表，delta 与祖先按业务键合并且新层优先，禁止父子 `UNION ALL` 产生重复事实。导出目标目录已存在时 fail-closed，必须换新 release tag；失败 summary 的 `all_ok=false` 不会成为最新事实源。
- 控制面 CTAS 收缩不保留序列/default；`initialize_database` 与收缩脚本会按保留表最大 ID 重建模型序列并恢复列默认值，避免收缩后重复 ID 或下一次 ORM 写入失败。

涨跌停价格：`limit_up` / `limit_down` 不再依赖供应商字段，改由 `app/market/price_limit.py` 按板块从当日 `pre_close` 推导（主板 ±10%、创业板/科创板 ±20%、北交所 ±30%、B 股 ±10%，主板 ST 降为 ±5%，ROUND_HALF_UP 到分）。锚点必须是**当日自身的 `pre_close`**，不能用前一行的 `close`——除权除息日两者不相等。指数（`.SH` + `000` 前缀，如 `000300.SH`）不套用涨跌幅带。

任何会进入策略、回测、组合和持仓推演的生产数据，仍应以经过质量校验和血缘追踪的 `market` / `factor` 层为准。

API 启动后的日线维护由 `scheduler/auto_data_sync.py` 的后台线程执行，调用
`services/auto_data_sync.py` 编排参考数据刷新、HiThink dump 幂等导入和可信日线构建。
全量 dump 的集合式 market 构建只负责可直接判定的基础行；存在第二来源、量额缺失或一零一正、停牌/非法 OHLC/负量额等硬拒绝、价格连续性/极端涨幅警告、以及 raw 早于自身交易日 15:30 上海时间采集的日期，必须在同一事务提交前回到公共质量与对账服务，保证批量和逐行路径产生相同的可信状态及问题证据。近期 QFQ 补采在下载前分页生成分类计划，只自动处理确认缺行和量额不完整；状态未知、上市日未知、状态冲突与其他 suspect 保持未解决，部分结果不得记为完整成功。
批量日线构建在存在任一独立 raw 或历史冲突的日期会再次执行通用多源对账；
真实冲突使已有非人工修复的 market 行降为 `suspect`，保留数值和 raw 血缘，
不再提供给可信读取。通达信目前承担独立对账，不承担主源接口失败时的自动切换。
首次使用历史包建立处理基线，之后按成功任务水位与近期包实际窗口判断增量或历史回补，
通过 `JobLog` 留痕；任务失败不推进水位。原始记录已经存在时仍可恢复缺失 market，
已人工修复的数据不被自动覆盖。自动链在未复权日线通过最新日股票覆盖闸门后，只继续到版本固定的 QFQ、curated 价格与因子值；不触发研究结论、组合或交易。因子族没有上游数据时记为 `empty/partial`，不得用零值补齐。
生命周期任务不阻塞 API 启动，退出时等待当前任务结束；单 worker 部署，避免并行写库脚本。

价值研究另设独立的晚间输入准备链。它先要求同日 `auto_data_sync` 成功且 SH/SZ/BJ 日历齐备，再将东方财富估值与业绩报表 ROE 的每页原始响应写入 `raw.vendor_snapshot`，批次记录在 `raw.import_batch`。目标交易日取上海本地当天且仅在 21:00 后采集，分页及发布前再次检查日期，跨午夜不发布；当前 clist 接口不可用时，只能从供应商标注**同日** `TRADE_DATE` 的估值分析分页退回，仍须与可信未复权收盘价相差至多 0.01 元。ROE 使用已公告的加权平均净资产收益率，季度初空报告期向前查找，保留报告期、公告日、披露时刻与原始页哈希；没有完整首次可得和修订版本证明。分页总数、去重、沪深北各自的证券及日线覆盖、供应商市场码、价格、有效 PE/PB 与 ROE 覆盖验收通过后，才写入带 `_research_batch_id` 的研究观察行；通用估值因子、市值读取和晚间 ROE 因子写入均与之隔离。失败批次保留供审计。旧东方财富估值直写入口已禁用。午夜报告要求同日成功准备任务和绑定的 raw 批次，缺输入时阻断，不使用旧日估值；该报告不获得历史 PIT 或自动执行资格。证券参考数据本身也先入 raw，HiThink 上市日低覆盖时有限重取，未知 ST 仍逐股阻断。

指定历史交易日的补采另走东方财富估值分析日期接口，独立批次只写 raw 和任务日志，逐页保存原始 JSON、响应哈希与真实 UTC 观察时间。供应商 `TRADE_DATE` 是其数据标签，不证明该版本在当日已可得。当前通用估值因子按交易日期消费未标研究隔离的 `market.valuation_snapshot`，因此历史补采在可得时间和修订血缘落地前不得发布到 market、factor、筛选或回测。

**估值历史研究层（2026-10-26）**。上一段的禁止针对的是「发布到 `market` 可信层」，研究层另有落点：估值历史补采发布为温层 Parquet 研究表 `research.valuation_history`，由 `scripts/ingest/eastmoney/collect_valuation_history.py` 执行，判定标准见 `aiDoc/modules/schema-plan.md` §17。它按交易日逐日通过双闸门才发布——价格与可信日线**零容差**逐笔一致，且供应商股票池覆盖可信个股池不低于 95%（分母只含 `security_type='stock'`）；任一标的不符或同一 `(symbol, trade_date)` 出现重复，该交易日即被拒收。供应商分页必须用复合排序 `sortColumns=TRADE_DATE,SECURITY_CODE` + `sortTypes=1,1`：只按 `TRADE_DATE` 排序时服务端对同一交易日内部的行序不作保证，实测跨页重复 571 键并同量丢行，会以「覆盖率不足」的形式伪装成供应商缺数据。

该表的全部行 `pit_verified=false`：它们是今天对历史日期的观测，不是当时可知的证据。字段级判定为 `close` / `pe_ttm` / `ps_ttm` / 股本市值类为真 PIT，`pb_mrq` 按报告期末切换、最多前视 120 天因而**不可**作为可交易证据。消费路径是 `WarmResearchReadRepository.list_valuation_history` → `scripts/factors/compute_value_factor_ic.py` → `factor.factor_profile`；ORM 孪生 `app/repositories/research_valuation.py` 读控制面 `market.valuation_snapshot`，两处刻意的不等价（`pb_mrq` 报 `None`、`pit_verified` 报 `true`）由测试固定。ST 历史时点与行业时点仍不可得——供应商历史行的证券简称与行业板块是**当前值**（2018 与 2026 变化数均为 0），字段因此直接不取。详见 `aiDoc/data/20261026-valuation-history-backfill.md`。

### A 股公司行为日期补证据边界（2026-10-27）

同花顺调整事件已提供 A 股除权除息日、每股现金分红与送股比例；此处补采同一事件的登记日和派息日。

自动同步每轮结束时，可独立于当轮日线结果按缺日期证券限量抓取公司行为证据。每次来源尝试独立建立 `raw.import_batch`，将完整响应、UTC 观察时间、供应商版本与内容哈希保存到 `raw.vendor_snapshot`；同内容重采保留批次但不重复保存快照。失败只影响证据采集结果，不回滚已通过认证的价格/因子发布。采集所得是晚采历史原始证据，未完成公告时间、实施日期、金额单位和跨源事件配对的校验，不进入 `market.corporate_action`，不改变策略/回测的拒绝门槛。

### 多源证据兜底（2026-10-28）

公司行为、换手率和退市名单现由各自的 Service 按字段契约兜底，手工入口与自动同步复用同一 Service。分红顺序为 Tushare → 巨潮（AKShare）→ 东方财富（AKShare），东财缺派息日只能给部分证据；换手率按可信未复权收盘价核对后从 Tushare 切东财；退市名单从 Tushare 切交易所接口，其中上交所的“暂停上市日期”不能当退市日期。每次尝试保留批次、失败原因、接口和真实抓取时间，原始响应只进 `raw`。自动任务限量及冷却规则见 `aiDoc/data/source-ingestion-policy.md` §14–16；`market` 的字段级血缘和 PIT 认证尚未完成，这些证据不供因子与策略直接读取。
