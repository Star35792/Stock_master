# ADR：不可回放 WAL 的无损恢复与预防

> 后续策略覆盖（2026-11-12）：下文记录事故当时的自动备份流程；当前 `recover_duckdb_wal.py` 不再复制 DB/WAL，恢复后仍须校验行数并写审计。WAL 是已提交事务的组成部分，不得为节省空间直接删除。

- 日期：2026-11-06
- 状态：已接受并实施
- 决策级别：T1（存储可用性修复；不改 schema、不改数据契约、不改 API）

## 背景与触发原因

2026-11-06 执行「同步数据」时，主库 `data/stock_master.duckdb` **完全打不开**：任何连接（含只读）都报

```
INTERNAL Error: Failure while replaying WAL file "D:\code\stock_master\backend\data\stock_master.duckdb.wal":
Calling DatabaseManager::GetDefaultDatabase with no default database set
```

现场事实：

- 数据库文件本体完好。剥离 WAL 后可直接打开，`market.daily_bar` 到 2026-11-03（376,185 行）、`raw.daily_bar` 到 2026-11-03（467,075 行）。
- WAL 为 12.6 MB，是 2026-11-05 10:42 与 11:08 两次被中断的 `daily_loop` / `hithink_daily_sync` 留下的。
- 同一批被中断任务在 `metadata.job_log` 留下 `status='running'` 的孤儿记录（290/291）。
- 恢复后 `raw.vendor_snapshot` 由 8,908 增至 8,987——WAL 里确实有已提交未 checkpoint 的数据，**删 WAL 会丢数据**。

## 根因

DuckDB 上游缺陷 [duckdb/duckdb#18259](https://github.com/duckdb/duckdb/issues/18259)（修复 PR #21516 已合入 main，但未进 1.5.x 正式版；1.5.4/1.4.3/1.4.0 均可复现，2.0.0 已修复）：

WAL 回放发生在**默认数据库上下文建立之前**。若 WAL 中含有**带默认表达式**的 DDL 条目，回放时绑定该表达式需要 `DatabaseManager::GetDefaultDatabase()`，而此时尚未设置，于是抛出上述内部断言。数据库文件本身没有任何损坏。

实测收敛的触发条件（最小复现矩阵）：

| 场景 | 崩溃后重开 |
| --- | --- |
| `ALTER COLUMN id SET DEFAULT nextval('seq')` | **FAIL**（本次故障） |
| `ADD COLUMN ts TIMESTAMP DEFAULT now()` | **FAIL** |
| `ADD COLUMN flag BOOLEAN DEFAULT false`（常量） | OK |
| `CREATE TABLE ... DEFAULT now()` | OK |
| `ALTER ... SET DEFAULT nextval(...)` 后立即 CHECKPOINT | OK |
| 普通崩溃（无 DDL） | OK |

即：**只有「非常量默认表达式」的 ALTER 会触发**。

本项目的触发源是 `app/database/session.py::ensure_duckdb_sequences`：它每次启动都对 40 个序列列**无条件**重发

```sql
ALTER TABLE "<schema>"."<table>" ALTER COLUMN "<column>" SET DEFAULT nextval('<seq>')
```

默认值本已正确，这 40 条 DDL 纯属冗余，却把不可回放的条目写进 WAL。进程一旦在下次 checkpoint 前被强杀（`timeout`/SIGTERM/强退），数据库就再也打不开了。

## 决策

**恢复用 ATTACH 绕过，不删 WAL；预防靠模型层建表即带默认值 + 幂等 DDL + 立即 checkpoint。**

1. **无损恢复**：从 `:memory:` 连接 `ATTACH '<db>' AS r`，ATTACH 提供了默认数据库上下文，回放因此正常进行；随后 `CHECKPOINT target`（**必须点名目标库**，裸 `CHECKPOINT` 只作用于默认库即 `:memory:`，是空操作）把 WAL 折进数据库文件并清空 WAL。此后用原版本 DuckDB 直接打开即正常。已固化为 `backend/scripts/maintenance/recover_duckdb_wal.py`：区分「被其他进程占用」与「WAL 不可回放」（占用则直接退出；占用期间拷贝得到的是两个不同时刻的切片，回滚无法配对）；持主库写锁（`app/utils/duckdb_write_lock.py`，`timeout_seconds=0` 不排队）后再操作；ATTACH 回放并 checkpoint；恢复后校验 `raw.vendor_snapshot` 行数一致；并按 AGENT.MD「人工修复必须写审计」在 `metadata.job_log` 写一条 `job_type='repair'` 记录。2026-11-12 起脚本不再自动复制 DB/WAL。
2. **模型层建表即带默认值（根治）**：37 处序列主键列补上 `server_default=text("nextval('<seq>')")`（此前只有 9 处有）。`CREATE TABLE ... DEFAULT nextval(...)` 是**可安全回放**的，只有 `ALTER` 路径有毒。新库因此在 `create_all` 阶段就建出带默认值的表，**首次初始化零毒 ALTER**（实测 346 → 149 条语句，序列默认 ALTER 由 37 条降为 0 条）。
3. **幂等 DDL**：`ensure_duckdb_sequences` 先读 `information_schema.columns.column_default`，默认值已等价于目标 `nextval('<seq>')` 时**跳过** DDL。比较走归一化（去空白、统一大小写、剥 schema 前缀），不一致时打 WARNING——静默降级会让人误以为问题已解决。
4. **立即 checkpoint**：`initialize_database` 末尾调用公共 `checkpoint_duckdb()`，把 schema DDL 折进数据库文件，不留在 WAL 里。`slim_metadata_db.py` 单独调用 `ensure_duckdb_sequences`、绕过 `initialize_database`，因此**也显式调用同一函数**，不留未覆盖的写入路径。checkpoint 失败只记 WARNING 不阻断启动——checkpoint 失败时数据库本身仍健康，让 API 起不来代价更大。
5. **catalog 隔离**：DuckDB 的 `information_schema` 与 `duckdb_sequences()` 跨所有 attached catalog，查询必须带 `table_catalog = current_database()` / `database_name = current_database()`，否则 ATTACH 同名表时 `scalar_one_or_none()` 会抛 `MultipleResultsFound`。
6. **不升级 DuckDB 依赖**：2.0.0 虽已修复该缺陷，但属跨大版本升级，会牵动 `duckdb-engine` 兼容与存储格式，超出本次「同步数据」范围。ATTACH 方案在项目当前 1.5.4 上即可完成恢复，**无需任何依赖变更**。

## 验证

- **恢复**：主库经 ATTACH+CHECKPOINT 后可直接打开；`raw.vendor_snapshot` 8,908 → 8,987，11-05 已提交数据完整保留，零丢失。恢复脚本在构造的故障副本上端到端验证通过（含「未 checkpoint 的已提交行」确实保住的断言）。
- **预防（对照实验）**：临时禁用幂等守卫后，`test_fresh_database_needs_no_sequence_default_ddl` 与 `test_repeated_initialization_does_not_reissue_sequence_default_ddl` **双双失败**；恢复后通过。
- **回归测试**（`backend/tests/test_database_initialization.py`）：
  - `test_fresh_database_needs_no_sequence_default_ddl`——新库零序列默认 DDL，且 40 个默认值齐备；
  - `test_repeated_initialization_does_not_reissue_sequence_default_ddl`——二次初始化不再重发 DDL；
  - `test_sequence_default_ddl_is_never_written_to_the_wal`——崩溃后库仍可打开且数据可读。
- **全量**：1217 项 pytest 通过，ruff 干净。

## 边界与已知取舍

- **恢复脚本不改数据语义**：只回放已提交事务并 checkpoint，不新增、不修正任何行。删 WAL 才会丢数据，本方案明确不采用。
- **恢复需独占数据库文件**：DuckDB 不允许同一文件被两个句柄同时读写。脚本已做占用检测并拒绝在占用状态下动手。
- **孤儿 job_log 记录需人工收敛**：被强杀的任务留下 `status='running'` 的记录（如 290/291、292/293、294/295），须在确认对应进程已不存在后手工收敛为 `failed`，否则污染任务水位判断。孤儿 `daily_loop` 记录的 `metadata.target_trade_date` 仍可用于判断缺口日期。
- **`timeout` 强杀同步任务本身就是错的**：应让任务自然结束或用可捕获信号的方式中断，否则除 WAL 风险外还会留下孤儿任务记录。
- 上游缺陷仍未在 1.5.x 修复；若将来必须升级 DuckDB，需回归 `duckdb-engine` 兼容性，另按 T2 单独立项。

## 影响

- `aiDoc/memory/README.md`：更正原「必须移走 WAL」的结论，补充 ATTACH 无损恢复路径与触发条件收敛结论。
- `aiDoc/modules/module-index.md`：新增 `database.session` 与 `scripts.maintenance.recover_duckdb_wal` 模块条目。
