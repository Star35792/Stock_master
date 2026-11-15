---
doc_type: decision
status: current
as_of: 2026-10-24
note: >-
  决策不变；文中「6 张表」现为 7 张，研究库另含 strategy.external_observation。
---
# ADR：strategy 回测产物拆分独立 research.duckdb

- 日期：2026-10-24
- 状态：已接受并实施（用户决策「拆分文件，走 parquet」方向）
- 决策级别：T2（存储架构边界变化）

## 背景与触发原因

DuckDB 无服务器模式对同一数据库文件只允许一个读写进程，且该行为不可配置。实际冲突场景：研究脚本（回测/回填）长时间持有主库写锁期间，API 进程或另一个维护脚本被 `Unique file handle conflict` 拒之门外，工作白费。2026-10-24 已落地第一层缓解（跨进程写锁 `app/utils/duckdb_write_lock.py`，维护脚本排队），但排队不解决「研究跑 10 分钟、API 写路径全堵 10 分钟」的结构性串行。用户要求拆分文件并考虑 Parquet。

## 决策

**拆写域，不拆消费链**：

1. `strategy` 回测产物 6 张表（`backtest_run/trade/equity/position/performance`、`training_artifact`）整体迁出主库，落独立 `data/research.duckdb`（`settings.research_db_path`）：
   - 模型换挂独立 `ResearchBase`（`app/database/research_base.py`），独立 engine/session（`research_session.py`，NullPool——API 侧按请求连接，不常驻）。
   - 交易事实、市场、因子、组合、风控、paper、持仓账本全部留在主库不动。
2. **服务层兼容参数 `research_db=None`**：`DailyBacktestService.run`、`StrategyResearchService`（单窗/rolling）、`BacktestPerformanceService.analyze`、`BacktestQueryService`（benchmark/analyze 双 session）、`PriceValueBacktestService`、`OperationsService`（overview/alerts/failures/failure_detail）。`None` 回退调用方主 session——既有测试与脚本零改动，行为不变。
3. **API 路由**：`/backtests/*` 全部端点、strategy-research 三个写端点、operations 四个读端点接 `ResearchDatabaseSession`（独立 FastAPI 依赖）。混读端点（benchmark：行情主库 + run 研究库；analyze_performance：同构）显式双 session。**并发语义**：DuckDB 在他进程持研究库读写连接期间连只读打开都会被拒，因此 `get_research_db` 依赖先短超时（3s）获取 `research_write.lock`，拿不到直接 409 `research database busy`——研究脚本长跑期间 API 的 /backtests 查询得到明确的忙响应而非 500；脚本启动也会等 API 的短请求释放锁（秒级），双向互不静默破坏。
4. **研究脚本双 session + 研究锁**：`run_warm_factor_selection.py` / `run_warm_rolling_selection.py` 主库 session 只读行情/因子/日历，产物提交到 `ResearchSessionLocal`，写入期间持 `data/research_write.lock`。
5. **迁移**：`scripts/maintenance/migrate_strategy_to_research.py`（幂等，可 `--keep-main` 观察）。已执行：22 run/9,974 trade/1,080 equity/21,561 position/2 artifact 复制核对后主库旧表删除。注意点均已修复并记录：旧表 ALTER 追加列导致列序错位（按目标列名显式 SELECT）；ORM sequence 建在默认 schema（`nextval` 不带 schema 前缀），迁移后需在 research 库默认 schema 重建 `START max+1`（DuckDB 无 ALTER SEQUENCE RESTART）。
6. **锁域分离**：主库脚本持 `data/duckdb_write.lock`，研究脚本持 `data/research_write.lock`——两域并发不再互堵。
7. **测试**：conftest 把 research 依赖映射到同一内存 DuckDB（两套 metadata 表集不相交）；自建引擎的测试补 `initialize_research_database`。

## 验证

- 拆库前后同一请求结果逐位一致：every-20d 单窗净收益 +12.36%/夏普 1.42/佣金 6,237（`warm_fsel_ic_every20d_postsplit.json`），rolling 均值 +6.16%（`warm_roll_postsplit_verify.json`），零行为漂移。
- API 冒烟：`GET /backtests/runs`（读 research 库，run 30 为拆库后新 run）、`/operations/overview`、`/backtests/runs/14` 正常。
- 全量 733 测试通过，ruff 干净。

## 边界与已知取舍

- **跨库无事务**：run 记录（research）与任何主库写入不在一个事务里；回测产物本身是自包含事实，可接受（与持仓账本 ADR 同一论证）。
- **两个 DuckDB 文件不能被同一连接同时以读写打开的 ATTACH 限制不存在**（ATTACH 只读/读写同连是允许的），迁移脚本用 ATTACH 完成同连接复制；但 API 运行期间不要用另一个进程 ATTACH 主库做写迁移。
- **Parquet 查询面未做**（phase 2，可选）：若未来 API 查询端点成为 research 库的读瓶颈，按温层模式把 `backtest_*` 导出到 Parquet 供只读查询；当前查询量小，不引入双读路径复杂度。
- `research_read_source=warm` 的回测读路径不受影响（bars 走 Parquet、公司行为/日历走主库 ORM）。

## 影响

- `AGENT.MD` 仓库概览与工程规则补 research 库条目；`system-map.md` 事实源边界、`schema-plan.md` 表域、`module-index.md`、`README.md` API 说明同步。
- memory 记录 sequence 重建与列序两个迁移坑。
