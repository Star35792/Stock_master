# 分层存储架构

> 历史决策与后续覆盖：本文的“纯 DuckDB 单库、无 SQLite”是最初决策；当前增加了 [SQLite 持仓账本](20261023-1925-position-ledger-sqlite.md) 与 [独立研究 DuckDB](20261024-1427-research-database-split.md)。本文旧的备份和持久副本建议也已由本机单份保留策略覆盖。当前存储及温层发布状态以 [系统关系](system-map.md) 为准，以下原始取舍保留供追溯。

## 1. 决策

Stock Master 不再以 PostgreSQL 承载全部数据。面向**单机单用户**的个人量化场景，生产存储按职责分层，但只引入实际用得上的组件：

```text
外部数据源
  -> raw landing / cold archive (本地 FS)
  -> trusted warm datasets (Parquet)
  -> DuckDB query and transformation + control plane + transactional truth
  -> API / factor / backtest / portfolio / risk

DuckDB   = 单库同时承载控制面、事务事实与温层查询/转换引擎（个人单用户规模）
Parquet  = warm analytical facts（本地磁盘）
本地 FS  = 冷存储与不可变 landing（新闻等追加式原始文档同样先落此处）
```

存储只使用 DuckDB 单库 + 本地磁盘 Parquet/文件，不引入 MinIO 对象存储，也不引入 Redis 缓存层（决策见 §1.2）。

“可信事实源”不再等于某个特定数据库产品。可信性由数据契约、不可变版本、manifest、校验状态、血缘和发布指针共同定义。

### 1.1 关于控制面数据库的选型决策（T2）

评估过"SQLite 做控制面 + DuckDB 做温层"的拆分，结论是**当前阶段保持纯 DuckDB 单库，不引入 SQLite/PostgreSQL**：

- 全部 ORM 模型依赖 9 个原生 schema（`raw/market/fundamental/factor/strategy/portfolio/risk/trade/metadata`，64 处 `{"schema": ...}`）、`Sequence` 自增、`Numeric(24,4)` 定点和 `TIMESTAMPTZ`；换 SQLite 需重做 schema、自增与时区存储，成本高且属返工。
- DuckDB 的无行锁、无 SAVEPOINT 已被 `app/database/transaction.py` 的 `retry_on_conflict` 与 `idempotent_write` 填平，并有双会话同键并发测试覆盖；换库等于推倒重来再踩新坑。
- 单用户本地无高并发写，trade 写入量极小，乐观并发重试足够；DuckDB 单库同时服务 OLTP 元数据与 OLAP 扫描，省去跨引擎同步。

只有出现真正的多进程并发写、或 DuckDB 事务重试频繁失败时，才重新评估拆 SQLite。

### 1.2 关于 MinIO 与 Redis 的决策（T2）

个人单机场景**不引入 MinIO，也不引入 Redis**：

- MinIO：本地文件系统已提供持久性，MinIO 只多一层常驻服务、桶/凭证管理与故障面，与 §7"低运维复杂度"相悖，且无任何代码依赖它。新闻类外部因子（追加式不可变文档）虽是对象存储的典型场景，但个人 A 股新闻量级不大，按同一 raw→curated→factor 模式落成本地 Parquet/JSONL 即可；其真正需求是时点时间戳（防前视）、raw 不可变 + checksum 和 news-release→factor 血缘，与底层是 MinIO 还是本地盘无关。
- Redis：单用户本地查询量下 DuckDB 直接回源的延迟已可接受，缓存层是纯负担且无代码消费；一旦引入还要处理版本化失效与故障降级，收益为负。

只有真出现多机（研究机 + 调度机共享存储）、上云、或大规模冷归档时，才重新评估对象存储。届时 raw/冷存落地走一个薄 blob-store 接口（`put/get/exists/checksum`）留门即可（S3 API 配置切换，非重写），当前不提前构建。

## 2. 各存储职责

### DuckDB 控制面：控制面与事务事实

由 DuckDB 单库（`data/stock_master.duckdb`）经 SQLAlchemy ORM 承载小体量、强约束、需要行级更新或审计的记录：

- `system`/`metadata`：任务日志（`system.job_log`）、数据质量问题、数据源配置、数据集版本目录、发布指针。
- `market` 元数据：证券主数据、交易日历、人工修复指令、来源对账结论。
- `factor` 元数据：因子定义、运行记录、profile/诊断摘要。
- `strategy` 元数据：回测运行合同、参数、状态、绩效摘要；大体量逐日结果可迁往温层 Parquet。
- `portfolio`、`risk`、`trade`：计划、确认、风控、订单、成交、现金、持仓和审计，以 DuckDB 事务为事实源，写入经 `retry_on_conflict`/`idempotent_write` 保证幂等与并发安全。
- 大表热窗口：`market.daily_bar`、`raw.daily_bar`、`market.adjustment_factor`、`factor.factor_value` 在 DuckDB 中只保留近期滑动窗口（默认 90 天）用于幂等写入与快速对账；完整历史由 Parquet 温层承载，并通过 `WarmLayerStore` 对 reader 透明合并。

控制面与热窗口共库是个人单用户规模下的有意简化，非最终多机形态。

### DuckDB + Parquet：温数据主存储

Parquet 保存列式数据文件，DuckDB 负责查询、转换、质量检查和批量计算。主要数据集：

- `raw.daily_bar`、原始财务、原始估值、原始另类数据快照。
- `market.daily_bar`、公司行为、复权因子、财务事实、估值快照、另类数据事实。
- `factor.factor_value`。
- 回测逐日净值、逐日持仓、模拟明细之外的大规模研究中间产物。

默认分区：

| 数据集 | 建议分区 | 排序键 |
| --- | --- | --- |
| 日线/估值/因子 | `trade_year/trade_month` | `trade_date, symbol` |
| 财务事实 | `report_year` | `available_at, symbol, report_period` |
| 公司行为 | `event_year` | `effective_date, symbol` |
| 回测逐日结果 | `run_id` | `trade_date, symbol` |

禁止按单个 `symbol` 生成海量小分区。写入先进入 staging，校验通过后生成不可变 release，再原子切换 active manifest。修订数据生成新 release，不原地修改已发布 Parquet。

### 热读取：直接回源 DuckDB（不引入 Redis）

热读取（最新行情摘要、最近 K 线窗口、最新因子横截面/榜单、运维概览）直接查询 DuckDB，不设缓存层。个人单用户查询量下延迟可接受，且省去版本化失效与故障降级的复杂度（决策见 §1.2）。

### 冷存储与温层持久化后端：本地 FS

冷存与不可变 landing 落本地文件系统：

- 外部供应商原始文件和不可变 landing 对象（含未来的新闻等追加式原始文档）。
- 已发布 Parquet release 的持久副本。
- 历史 manifest、校验报告、回测/研究制品和备份。

访问按 `dataset/release/partition` 组织目录并对已发布文件记录 checksum，不得用覆盖同键模拟更新；active release 和审计引用的版本不得删除。

## 3. 数据集发布合同

每个温层 release 至少记录：

- `dataset`、`release`、`schema_version`、`storage_uri`。
- 来源对象及 checksum。
- `row_count`、日期范围、分区信息。
- `created_at`、`code_version`、质量状态。
- `parent_release`（增量或修订时）。
- Parquet 文件清单及每个文件 checksum。

发布顺序固定为：

1. 写 staging 文件。
2. DuckDB 校验 schema、主键唯一性、行数、日期范围和金融质量规则。
3. 校验通过后把文件移入 `dataset/release/partition` 持久目录并记录 checksum。
4. 在 DuckDB 事务中登记 release 并原子切换 active 指针。
5. 延迟清理旧本地缓存；不能先删旧版本再发布新版本。

## 4. 时点、精度与血缘

- 价格、金额和比率在 Parquet 中使用定点 `DECIMAL`，禁止为了方便统一转为 `DOUBLE`。
- 财务、估值和事件数据保留 `source_available_at` / `first_seen_at` / `effective_at`，因子只能读取计算时点已可得版本。
- 每条因子 release 绑定输入 market release、因子定义版本、代码版本和参数。
- 人工修复写 DuckDB 审计记录，并据此重建新的温层 release；不得直接编辑 Parquet 文件。
- 回测与风控必须绑定明确 release，禁止使用无版本的“最新数据”重放历史结果。

## 5. 切换顺序

已按 clean cut 完成第一步：历史 PostgreSQL 分析数据不迁移，只保留字段契约、质量规则、血缘语义和事务域表结构，全部 ORM 已切到 DuckDB 单库并从外部源重建分析数据。剩余步骤（温层接入日循环、Parquet release）按下列顺序推进：

1. （已完成）DuckDB 单库承载 `raw -> market -> factor -> ... -> trade` 全链路与控制面，`daily_loop` 串联 `daily_sync` 与 `factor_pipeline` 并写 `system.job_log`。
2. （已完成）`curated/prices_qfq` 接入 `daily_loop`：当 `factor_reads_curated=true` 且 `factor_adjust_type='qfq'` 时，`daily_loop` 在同步后基于 `market.daily_bar(adjust_type='none')` 与 `market.adjustment_factor(adjust_type='qfq')` 重建前复权 Parquet，并携带逐日真实 `qfq_factor` 与 `turnover_rate`。重建是幂等的（先写临时目录再原子替换），失败会中断后续因子计算。QFQ 行不再物化到 `market.daily_bar`。
3. （已完成 P0.2a）`lake.release` 记录 market/curated 数据集的内容寻址 `release_id`、schema、日期范围、行数、checksum、父版本、代码版本、质量状态和 active 指针。日循环先登记 pending release 并把 curated release 绑定到 `factor.factor_run.release_id`；认证门禁通过后才原子切换 active，失败时旧 active 继续服务。`market.adjustment_factor.release_id` 记录复权版本血缘。
4. （已完成 Phase 1）`market.daily_bar`、`raw.daily_bar`、`market.adjustment_factor`、`factor.factor_value` 已按 `trade_year/trade_month` 分区导出到 `data/warm/exported/<release_tag>/` 作为只读 Parquet 温层，并附 `manifest.json` 与 SHA-256。详见 [`20261021-1045-storage-split-phase1.md`](20261021-1045-storage-split-phase1.md)。本阶段不删除源表、不改应用读路径。
5. （已完成 Phase 2）控制面 DuckDB 已瘦身至热窗口（约 90 天），4 张大表历史数据由 Parquet 温层承接；新增 `WarmLayerStore` 在内存 DuckDB 中把 Parquet 历史与 `ctrl` 热窗口合并为原 `schema.table` 视图，读路径无需改动。`daily_loop`、因子计算、低价超跌反弹回测均已验证通过。详见 [`20261021-1150-storage-split-phase2-warm-layer.md`](20261021-1150-storage-split-phase2-warm-layer.md)。
5.1 （已完成 2026-11-09）控制面**空闲空间回收**：DuckDB 删除行后不收缩文件，实测主库 2002 MiB 中仅约 310 MiB 是活跃数据（84% 为已删除行留下的空闲块）。新增 `backend/scripts/maintenance/compact_control_plane.py` 做只回收空间、不改任何数据行的重构式瘦身（2002 → 1470 MiB），并修复了 CTAS 重建遗留的唯一索引与序列丢失问题（恢复 40 个序列、22 个唯一索引、133 个带默认值列）。与 `slim_metadata_db.py` 分工：后者搬数据出控制面，前者只压缩空间。详见 [`20261109-1648-control-plane-compaction-and-index-recovery.md`](20261109-1648-control-plane-compaction-and-index-recovery.md)。
6. （待 Phase 3）完全迁移写路径到 Parquet：`raw.daily_bar`、`factor.factor_value` 改为追加写入 Parquet staging 并注册 release；`market.daily_bar`、`market.adjustment_factor` 保留 DuckDB 近期滑动窗口用于幂等写入。当前写路径仍经 SQLAlchemy ORM 写入控制面热窗口，再由 `WarmLayerStore` 读取完整历史。
7. 财务/估值/另类数据（含未来新闻因子）和回测明细按相同 raw→curated→factor 模式重建。
8. raw 不可变落地直接写本地 FS 的 `dataset/release/partition` 目录并记录 checksum；不引入 MinIO/Redis（见 §1.2）。

## 6. 验收标准

- 新环境从空库重建后，核心 API、因子和回测可以使用新数据工作。
- DuckDB 中的交易、风控和审计事务行为在换库后保持不变（幂等、并发安全、可追溯）。
- 相同温层 release 的 DuckDB 查询结果可重复，重启后仍可读。
- 发布流程在本地 FS 上保持原子性：release 未完整写入并登记前不切换 active 指针，中断不产生半发布状态。
- 任意回测结果可追溯到输入对象 checksum、温层 release、代码和参数版本。

## 7. 明确非目标

- 不把 DuckDB 当多写高并发 OLTP 数据库；个人单用户规模下以乐观并发重试兜底即可。
- 不为控制面额外引入 SQLite 或 PostgreSQL（见 §1.1）。
- 不引入 MinIO 对象存储与 Redis 缓存层（见 §1.2）；只用 DuckDB 单库 + 本地 Parquet/文件。
- 不保留或搬运历史 PostgreSQL 分析数据；只保留结构与契约并重新采集，也不建设双写兼容层。
- 不在首阶段引入 Spark、Iceberg、Delta Lake 等分布式组件；优先保持 DuckDB + 本地 Parquet 的低运维复杂度。
