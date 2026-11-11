# ADR：控制面主库空闲空间回收与索引/序列恢复

- 日期：2026-11-09
- 状态：已接受并实施
- 决策级别：T1（存储空间修复；不改 schema、不改数据契约、不改 API）

## 背景与触发原因

2026-11-09 例行巡检发现控制面主库 `backend/data/stock_master.duckdb` 占用 **2002 MiB**，而项目长期记忆记录的三阶段瘦身（4.3 GiB → 535 MiB → 665 MiB）早已失效。

实测根因与「库变大了」这一表象完全不同：

| 指标 | 数值 |
| --- | --- |
| 文件大小 | 2002 MiB |
| 全部表占用块数 | 1240 块（× 256 KiB ≈ **310 MiB**） |
| 文件尾部 4 MiB 零字节占比 | 49.8% |
| 实际活跃数据 | **约 310 MiB** |

即 **约 84% 的文件是 DuckDB 删除行后从未回收的空闲空间**，而非数据增长。DuckDB 不收缩数据库文件：删除行只把块标记为可复用，后续写入会复用它们，但文件体积永不回落。此前的 `slim_metadata_db.py` 通过 CTAS 重建文件，能压缩一次；但此后 `daily_loop` 持续写入、估值历史补采等操作又不断制造删除，于是体积缓慢回升。

## 决策

新增 `backend/scripts/maintenance/compact_control_plane.py`，做**只压缩空间、不动任何数据行**的重构式瘦身，并与 `slim_metadata_db.py` 明确分工：

| 脚本 | 职责 | 数据行 |
| --- | --- | --- |
| `slim_metadata_db.py` | 把大表历史**搬出**控制面到温层 Parquet | 减少 |
| `compact_control_plane.py`（本次） | 回收**已删除**行留下的空闲空间 | **完全不变** |

## 实施过程中的三个关键发现

### 1. 控制面早已丢失全部唯一索引与序列（既存缺陷，非本次引入）

重建时逐一比对备份发现：**压缩前的原始库索引数 = 0、序列数 = 0**（`stock_master-pre-compact-20261109-082805.duckdb` 与更早的 `pre-slim` 备份均如此）。

根因是 `slim_metadata_db.py` 的 CTAS 重建路径：`CREATE TABLE AS SELECT` 会丢弃 `PRIMARY KEY` 序列、`UNIQUE` 约束与列默认值，而该脚本复制的索引/默认值逻辑在 ATTACH 场景下因 `current_database()` 解析到错误 catalog 而静默失败。

**影响**：`repository` 层依赖的 `ON CONFLICT` upsert 在没有唯一索引时**静默失去去重保证**——重复行会被写入而不报错。这是比空间占用更严重的隐患。

**修复**：压缩脚本在发布前显式恢复

- 40 个 ORM 定义的序列（`ensure_duckdb_sequences`，起始值严格高于各表 `max(id)`，且列默认值绑定到对应序列）；
- 20 张表 ORM 声明的唯一约束（以唯一索引形式落地；已存在等价索引时复用而非重建，避免同一约束占用双份空间）。

恢复后校验：40 序列全部 `start_value > max(id)` 且默认值正确引用；22 个唯一索引；133 个带默认值列。

### 2. `lake.release` 存在既存的 `release_id` 重复

创建 `lake.release` 的唯一索引时报 `Data contains duplicates on indexed column(s)`。查证为**既存数据问题**：`market.daily_bar_qfq` 与 `curated.prices_qfq` 两个不同数据集共用了同一 `release_id`（15 行中 4 组重复）。

压缩脚本对此**只告警不阻断**（该索引被跳过并记录 warning），不擅自改数据。是否去重或放宽约束应由独立的修复任务决策。

### 3. ATTACH 场景下 DuckDB 的 catalog 限定是必需且易错的

三个实现细节踩坑，均已在脚本中固化注释：

- **catalog 限定**：ATTACH 的库必须写三段式 `"catalog"."schema"."table"`；写 `target.schema.table` 会报「schema does not exist」。
- **`information_schema` 不可三段式限定**：应写 `FROM information_schema.columns WHERE table_catalog = 'source'`。
- **序列 DDL 需重写**：源库的 `CREATE SEQUENCE x` 不带 catalog 前缀，直接执行会把序列建到 source 库，导致目标表的默认值绑定不到本库序列。脚本用 `_qualify_sequence_ddl` 改写为 `CREATE SEQUENCE target.x`。
- **导入 `app.database.session` 会占用文件句柄**：该模块在导入时即建立读写引擎，之后 DuckDB 拒绝以只读方式再次打开同一文件（`Unique file handle conflict`）。脚本因此全程只用 DuckDB 原生连接，ORM 引擎推迟到最后一步使用。

## 验证结果

| 项目 | 压缩前 | 压缩后 |
| --- | --- | --- |
| 文件大小 | 2002 MiB | **1470 MiB**（-26.6%，回收 532 MiB） |
| 表数 | 40 | 40 |
| 总行数 | 7,297,230 | 7,297,230（逐表比对 **0 差异**） |
| 序列 | 0（既存缺陷） | **40**（已修复） |
| 唯一索引 | 0（既存缺陷） | **22**（已修复，`lake.release` 因既存重复跳过） |
| 带默认值列 | 0 | **133** |

- 全量 pytest：**1217 passed**（90.5s）。
- 温层合并读路径：`market.daily_bar` 10,415,923 行（2016-10-26 ~ 2026-11-06）、`factor.factor_value` 10,487,817 行、`raw.corporate_action` 455,990 行（1991-02-26 起），无断档。
- ORM 写入端到端：`INSERT INTO metadata.job_log` 自增 id 由序列正常生成，回滚正常。

## 备份策略

本次同时清理了备份目录：原有 5 份 `pre-slim` 备份 **MD5 完全相同**（`ab9a252a...`），实为同一文件的重复拷贝，删除 4 份保留 1 份。

清理后保留三个 MD5 互不相同的、对应不同状态点的备份：

| 备份 | 大小 | 保留理由 |
| --- | --- | --- |
| `stock_master-pre-slim-20261021-062415.duckdb` | 966 MiB | 三阶段瘦身前状态 |
| `stock_master-pre-wal-recovery-20261106-142129.duckdb` | 1.44 GiB | WAL 事故恢复前状态 |
| `stock_master-pre-compact-20261109-082805.duckdb` | 2002 MiB | 本次压缩前状态 |

备份目录：6.9 GB → **5.2 GB**。

## 遗留事项

1. **`lake.release.release_id` 重复**——需决定去重（按 dataset + release_id 拆列）或放宽唯一约束。
2. **备份保留策略脚本化**——本次为手工去重，建议补 `scripts/maintenance/prune_backups.py`，按「同前缀保留最新 N 份 + 总容量上限」自动清理。
3. **`data/repair/`（1012 MiB）与 `data/price_value_screens/`（600 MiB）** 的中间产物清理策略待定；`data/` 目前同时承担「数据」与「垃圾场」两种语义。
4. **Alembic 缺失**依旧（`backend/alembic/` 不存在，CI 无 `alembic check` 步骤），schema 变更仍无版本化迁移与回退。
