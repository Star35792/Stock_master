# backend · 模块规则

作用域：`backend/**`。跨模块规则见根 [`AGENTS.md`](../AGENTS.md)；本文件只写后端专属的命令与已验证事实，不重复根规则。

## 命令（工作目录 `backend/`）

- 依赖：Python 3.13（`.python-version`，`requires-python >=3.13,<3.14`），`uv sync --extra dev`。
- 本地全门（与 CI 同一入口）：`uv run --extra dev python ../scripts/ci_check.py --scope backend`。
- 只 lint：`uv run --extra dev ruff check app tests scripts ../scripts/ci_check.py`（line-length 100、`select = E,F,I,UP,B`）。
- 只测试：`uv run --extra dev pytest -q --basetemp=.pytest_tmp/ci`。必须带仓库内 `--basetemp`：宿主的批量删除守卫会在 unlink 预算耗尽时中止进程，把无关用例变成非断言型假失败。
- 起服务：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`。只能用单 worker：三个后台 worker 在 `lifespan` 内启动，多进程会各自重复跑同步与筛选任务。

## 结构事实

- `app/` 的主要入口：`api`、`schemas`、`services`、`repositories`、`models`、`database`、`config`、`scheduler`、`data_sources`、`lake`、`market`、`factors`、`indicators`、`strategies`、`backtest`、`portfolio`、`risk`、`trade`、`utils`。实际文件与数量按代码核查，不以本规则中的快照代替。
- `app/api/router.py` 统一挂 `/api/v1`；路由与端点以当前代码为准。
- 存储路径与事实源边界见根 `AGENTS.md`；跨年历史在 `data/warm` Parquet 温层。
- 智能体状态使用独立 SQLite `data/agent.db`，独立 `AgentBase` 与 `initialize_agent_database`；会话、显式记忆与计划检查点均保存在此库，写执行默认关闭，阶段范围见 `aiDoc/plans/` 的整体实施计划和阶段决定。
- schema 由 `app/database/session.py::initialize_database` 维护（`create_all` → `ensure_duckdb_sequences` → `apply_schema_compatibility` → `checkpoint_duckdb`）；仓库内已无 Alembic 引用。
- 写互斥：主库 `data/duckdb_write.lock`、研究库 `data/research_write.lock`、任务租约 `data/.job_leases/process-slot-NN.lock`（`app/services/job_lease.py`）。
- 事件回测引擎位于 `app/backtest/`；当前版本以代码和研究结果中的版本字段为准。
- `backend/scripts/` 是一次性维护与研究脚本，写库前必须持对应域锁，且不得被 `app/**` 反向 import。

## 后端专属坑

- DuckDB 不允许同一文件被第二个句柄以只读再打开：`import app.database.session` 就会建立读写引擎。需要「先只读读、后 ORM 写」同一文件的脚本必须把 ORM 引擎推迟到最后一步，或全程用 DuckDB 原生连接。
- `session.merge(Model(...))` 不带主键会退化成 INSERT，重跑撞唯一约束，导致修正后的证据写不进去；证据落库走仓储 `upsert()`（`ON CONFLICT DO UPDATE`）。
- `corr()` 等聚合对退化分组返回 NaN 而非 NULL，写进 `DECIMAL` 列前必须过滤 NaN/inf。
- API 测试不要在请求线程里发真实 DuckDB SQL：`:memory:` 引擎按线程隔离，anyio 工作线程会拿到空库；照既有做法 monkeypatch 路由 service 或仓储。
- 温层 SQL 的 `OVER (ROWS BETWEEN n PRECEDING ...)` 在分区行数不足时按现有行数计算，与 ORM 因子实现的 fail-closed（窗口不满返回 None）语义不同；回填新因子要加满帧守卫（`count(*) OVER frame = n`）。
