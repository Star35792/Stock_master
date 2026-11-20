# 系统关系 Reference

## 主数据链路

```text
外部来源
  -> raw
  -> market
  -> factor
  -> research/backtest
  -> portfolio/risk
  -> trade
  -> operations / frontend
```

## 事实源边界

- 2026-11-19 用户决定新增第四个库：SQLite `backend/data/agent.db`，仅保存智能体会话、计划检查点和用户显式记忆，独立 `AgentBase`。只保留有界工具摘要和来源 ID；业务任务状态和研究结果仍以原库为准。SQLite 与业务 DuckDB 无跨库原子事务；未知创建结果必须核查原任务，当前第一阶段保持阻断而不重放。整体范围和后续阶段见[整体计划](../plans/20261119-1049-agent-overall-plan-and-sqlite.md)。

- 智能体第四阶段的CAS尝试检查点与核验摘要仍只存agent.db；任务/研究/组合保持各自原事实源。确认ID精确核查不依赖最近任务列表，研究产物首次写入即绑定原确认及参数哈希；跨库半提交只展示原产物并阻断，不重放业务写入。权限执行仍默认关闭，详见[恢复证据决定](../plans/20261119-1448-agent-recovery-and-evidence.md)。
- 第二阶段计划与批准存入既有会话 JSON，不新增表。`agent_planning.py` 校验白名单/参数/输入来源/DAG/有限绑定；`AgentService.plan/decide_plan` 负责版本冻结、失效和CAS。生成和批准不调用业务服务，批准不授予写权限。参见[阶段2决定](../plans/20261119-1405-agent-reviewable-plans.md)。
- 第三阶段`agent_execution.py`在原会话JSON保存有界执行检查点，`agent_permissions.py`在执行适配器和后台重检持久授权；每次I/O前CAS claim，解锁调用后重读合并。写提案绑定计划/步骤/参数/预检/代码/期限，批准与逐项写确认分开，默认写关闭。原任务与研究结果仍由原库承载；未知结果不重放，见[阶段3决定](../plans/20261119-1457-agent-permissions-and-execution.md)。

- 主 DuckDB（`backend/data/stock_master.duckdb`）保存市场、因子、组合、风控、模拟推演与业务控制面事实；独立研究 DuckDB 保存策略产物，两个 SQLite 分别保存手工持仓账本和智能体状态，本地 Parquet 保存温层历史及 curated 数据集。四库与 `warm/` 物理上都在 `backend/data/`：`settings` 里的 `data/...` 默认值是相对当前工作目录解析的，因此任务必须从 `backend/` 启动；从仓库根运行会另建一套 `data/` 与另一把同名写锁文件，两把锁互不相识。回测与因子研究的**读**路径可以直接读温层 Parquet（`research_read_source=warm`），**写路径始终只走 ORM**；温层连接不 ATTACH 控制面文件（`attach_control=False`），公司行为与交易日历仍从 ORM 读。
- 本地单份保留指各项必要事实只有一份权威数据，不再默认生成整库备份。`curated/prices_qfq` 发布只在切换期间暂存旧目录，成功后删除；失败构建和旧暂存定期清理。`warm/exported` 的有效父链、增量层、active 与审计引用仍是事实组成部分，清理时必须按引用关系而非目录年龄判定。
- 历史补齐用的 `_rebuild_curated_qfq_supplement.py` 同样在行数校验后通过共用切换逻辑发布，成功后不保留 `prices_qfq.prev-*` 副本。
- `price_value_screens/` 只保留最新全量研究报告和对应的冻结 cohort；旧 `price_value_screen` 任务日志保留日期、状态、数量与 `snapshot_hash` 等摘要，并移除已清理文件的路径引用。报告与 cohort 的当前引用先落库，再清理旧文件。用户已接受旧全量报告无法精确回看的取舍。
- 温层 Parquet 除 curated 数据集外还承载**研究层表**：只存在于 Parquet、无 ORM 模型、无 Alembic 迁移、不出现在控制面 `information_schema`。当前有一张：`research.valuation_history`（东财估值历史序列，全部行 `pit_verified=false`，只用于研究口径）。判定标准见 `aiDoc/modules/schema-plan.md` §17，字段级 PIT 判定与偏差登记见 `aiDoc/data/20261026-valuation-history-backfill.md`。研究层表不得被 `market` 层的通用读取器拾取。
- 手工记账的真实持仓账本存独立 SQLite `backend/data/positions.db`（`positions` 域，2026-10-23 决策，见 [ADR](20261023-1925-position-ledger-sqlite.md)）；账本持仓的估值价格仍取 DuckDB `market` 可信层，持仓与盈亏由流水重放推导、不落库。
- strategy 回测产物（`backtest_run`、`backtest_trade`、`backtest_equity`、`backtest_position`、`backtest_performance`、`training_artifact`，以及外部连接器观测快照 `external_observation`，共 7 表）自 2026-10-24 起存独立 `backend/data/research.duckdb`：研究脚本持 `backend/data/research_write.lock` 写产物，API/维护进程写主库，两域不再竞争同一把文件级写锁（见 [ADR](20261024-1427-research-database-split.md)）；行情/因子/日历读路径不变。维护/研究脚本写任一库前走 `app/utils/duckdb_write_lock.py` 对应域锁。
- `raw` 保存来源、批次、原始 payload 和异常数据。
- HiThink API 的 14 日回看及公司行为 dump 重跑只追加同源同键的新内容版本；相同内容只留一份 raw 行，批次仍记录本轮观测和跳过数量。`market.daily_bar` 对值与选中 raw ID 均未变化的行不重写，避免重复同步持续制造主库空闲块。历史重复行尚未按此规则回收。
- `market` 是可信消费层。
- `factor` 保存可复现的因子定义、运行和值。
- `strategy` 保存回测运行、成交、净值、持仓和绩效。
- `portfolio` 保存不可执行草案，`risk` 保存绑定服务端事实的评估，`trade` 保存短时确认和 approval 唯一绑定的 paper 执行批次。公共执行必须按 `plan -> equity/readiness -> risk evaluation -> approval -> execution batch` 单向消费，不能绕过确认直接调用旧纸盘内核。`trade` 在确认与执行两阶段都按 `market.daily_bar`（`adjust_type=none`）执行 `AShareTradabilityPolicy`；执行批次哈希显式包含 `execution_day_bar_hash`，把批次绑定到预演时的执行日行情快照
- `system` 是任务、质量与审计的业务模块名，相关 ORM 表实际位于 `metadata` schema（如 `metadata.job_log`）；文档中的 `system.*` 为历史逻辑域称呼，编写 SQL 时应使用物理表名。

## 工具边界

- 智能体Skill为`backend/app/agent_skills`发布的方法资料，正文/Markdown引用仅进瞬态模型上下文；agent.db保存身份和有界检查摘要，业务事实仍取原服务。计划绑定包哈希，批准/推进/确认/后台均重验，加载不能产生授权；只读补查受明确资源、轮数与去重限制，计划不自动追加步骤。见[技能决定](../plans/20261120-1233-agent-skills-and-evidence-loop.md)。

- DuckDB 写入协调采用操作系统锁：同路径常驻 `.guard` 文件维持稳定锁对象，PID 文件仅作诊断，持有时间不构成抢锁理由；退出或进程崩溃后 OS 释放锁。不要删除 `.guard` 文件。兼容旧 PID 文件时，存活或未知持有者保持阻断。研究库启动初始化与请求访问共同协调研究锁；分库集成测试的主库不建立 strategy 表。
- `metadata.job_log` 的新 `running` 记录必须绑定 `_job_lease.version=process_slot_v1`。每个产生日志的进程先占用主库同目录 `.job_leases/` 下 32 个固定 OS 锁槽之一；首次占槽时先在锁内收敛该槽旧 owner 遗留的 leased-running，再允许写新记录。API 与长期 scheduler 启动也扫描空闲槽。只有成功取得已登记槽的 OS 锁并在事务内二次确认仍为 `running`，才写 `failed + completion_status=unknown`；时间、PID、缺 guard、坏元数据或未知版本都不是死亡证据。占槽恢复只在本进程会话无待写事务时执行，避免提前提交调用方业务写入。该租约只证明本机 owner 进程是否仍持锁，不是任务重试、分布式租约或 DuckDB writer mutex，也不授权清理任务可能产生的 raw、release、WAL、临时文件或研究产物。旧版无租约记录只能通过精确 ID、默认 dry-run 的维护命令由操作人显式收敛；带租约但槽不可核验（缺 guard 或元数据非法）的记录，操作人额外加 `--confirm-owner-exited` 才可收敛，活锁与可自动恢复的记录即便带此参数仍被拒绝，杜绝与运行中 owner 或启动扫描争抢。`--list` 只读报告每条 running 记录的可关闭分类。

- `backend/app/lake`：本地 Parquet 数据湖和批量扫描工具；curated 数据集是因子服务可配置的生产消费源，与 DuckDB 事实层共同组成单机存储架构。
- 数据湖离线分区压缩先写同级暂存目录、核对行数，再切换目录；`daily_loop` 在文件切换后暂留旧目录，只有两条 release 激活事务提交成功才回收，激活或提交失败则恢复旧目录并保留新暂存供诊断。顶层目录重命名在 Windows 文件句柄短暂延迟释放时进行有界重试，持续占用仍失败并恢复原目录。普通构建不按名称或年龄自动清理历史目录。
- 控制面 `lake.release` 的新 release ID 绑定数据集、内容校验、schema、日期范围、行数、父 release、代码版本与元数据。完全相同的重跑复用原行；升级前的短 ID 若事实字段完全相同也优先复用，字段不同才产生新 ID。任何已存在 ID 的事实字段冲突直接失败，`rejected` 不复用也不激活。旧版短 ID 与原父链原样保留，不做启动迁移；active 与质量状态仍由显式激活管理。
- Warm release 表带 `snapshot/delta` 模式：完整快照截断同表父链，增量表按业务键由新层覆盖旧层；`all_ok=false` 的失败 release 不参与 latest 解析。同 tag 发布禁止覆盖（含东财估值历史采集），发布前检查父链所有节点及 16 层上限；读侧遇父链循环或超限直接报错，不静默截断。
- 温层大表导出先占用新的 release 目录，逐表行数校验失败即停止后续大表写入；全部通过后才原子写入成功 summary。导出失败清理本次未发布目录，避免留下整套 Parquet；硬中断或删除受阻仍可能留下无有效 summary 的目录，需后续核对清理。历史失败目录也须按引用与状态核对后单独清理。
- `libs/hithink_sdk/marketdb`：DuckDB SDK/toolkit，适合 notebook 和历史数据分析，不等同于后端 `market` 可信层。后端不 import 它（HiThink 取数走 `app/data_sources/hithink.py` 自有 urllib 实现），它也不参与 `scripts/ci_check.py` 的任何门命令。
- `libs/a_stock_data/loader`：把未入库的 `a-stock-data-main/SKILL.md` 内嵌 Python 用 AST 白名单净化成可 import 模块，供 `data_sources/tencent.py`、`tdx_official.py` 与对账脚本真实 import。不变式：导入时不得发起任何外网请求（上游代码块常在模块级直接取数并打印）。它是外部实现的适配层，不是第二个事实源，产出仍只进 `raw`。
- 检查门：本地与 CI 共用 `scripts/ci_check.py`（backend = Ruff + 全量 pytest；frontend = `bun test` + `bun run build`），前端唯一运行时是 Bun（版本 pin 在 `frontend/.bun-version`），步骤契约由 `backend/tests/test_ci_check_runner.py` 锁定。
- API 生命周期管理 `AutoDataSyncWorker`：启动后在后台线程串行执行全市场未复权日线同步；股票横截面覆盖达标后触发 `daily_loop(skip_sync=true)`，依次更新 QFQ、暂存 curated 价格、计算因子并经发布闸门原子切换。行情日期未前进但该日没有已发布闭环时仍会重试。每次结束后等待配置间隔再检查，关闭时等待当前任务完成；使用单 worker，任务与水位复用 `JobLog`（物理表 `metadata.job_log`）。
- HiThink dump 的批量日线构建保持集合式历史导入；存在任一第二来源、量额缺失/一零一正、停牌或其他硬拒绝、价格连续性/极端涨幅警告、历史盘中采集时点的日期在提交前复用公共质量与对账服务。冲突、警告及硬拒绝行保留原始值与 raw ID 供审计但退出可信消费路径，重复复核只复用未关闭的同类问题。通达信目前仍由独立采集入口获取，是对账源而非自动故障切换源。
- 近期 QFQ 缺口补采先分页生成分类计划：无行和量额缺失可进入自动采集，其他 suspect 仅诊断；明确停牌只接受当天观察或显式有效区间，上市日未知保持证据不足。部分来源或部分键失败记 `partial`，相同 raw 内容幂等复用并继续允许重试 market 构建。补采截止日与逐证券同步共用上海时间已完整过去日期。
- 可选的证据补采在每轮自动同步结束时独立执行：公司行为按 Tushare → 巨潮 → 东方财富逐证券兜底；换手率按 Tushare → 东方财富逐证券兜底；退市名单每天最多一次，Tushare 全市场响应复用给沪深两所，缺口再走交易所接口。每次尝试各写 `raw.import_batch`，成功响应写 `raw.vendor_snapshot`；同一数据源故障不会回滚已认证的日线/因子发布。字段缺失时只返回部分证据，三条链均不自动写 `market` 或解锁策略/回测。
- 同进程 `PriceValueInputPrepWorker` 于交易日 21:00 后准备当晚估值观察值与供应商披露 ROE，不生成通用 ROE 代理；`PriceValueScreenWorker` 在 00:00 生成上一完成交易日的只读全市场报告，并于启动和白天补查。三个 worker 共用进程内数据库写锁；仅适用于单 API worker，其他独立写库进程仍须避开。HiThink 证券与日历刷新先留 raw，证券上市日低覆盖时重取小页，实际变化才写 market，已知上市日冲突即失败。东方财富当前估值接口失败时，同日晚间可使用供应商标注**当天**交易日期的估值分析分页；估值与 ROE 原始页先入 `raw`，同日可信日线及公告时间验收后才附入研究用 `market.valuation_snapshot`。这些行带 `_research_batch_id`，通用估值/市值因子读取会排除；报告仅作人工复核。旧估值直写入口已禁用。
- 手工历史估值补采脚本按供应商 `TRADE_DATE` 获取估值分析日期列表，只向 DuckDB `raw` 留存独立批次和真实抓取时刻。因尚无历史首次可得时间与生产因子消费隔离，它不发布 `market`、`factor` 或筛选报告；运行脚本需与持有 DuckDB 的 API 进程错开。
- 腾讯行情的**两个价格字段都相对「厂商行情日」定义，行情日在 09:15 滚动**。取哪一天收盘由 `close_field_for_quote_day()` 按日历与时刻判定，禁止写死字段；选中的字段再与可信收盘实测校验，一致率不足即中止。厂商的市值与 PE/PB 按行情日价格计价，写库前重述到所选收盘口径。目标日只取「严格早于当前北京自然日」的最后交易日。详见 `aiDoc/relations/20261112-1005-tencent-quote-day-roll.md`。
- 独立调度脚本仍基于 APScheduler blocking scheduler；`daily_loop` 编排器既可自行串联逐证券 `daily_sync`，也可消费已通过覆盖闸门的全市场 dump。它以供应商 QFQ `hithink_finance_dump@v1` 为统一版本，curated 临时数据与目标日 7 个生产价量因子均通过认证后才发布；零行族记 partial。不要与 API 自动同步同时对同一 DuckDB 文件运行独立写库进程；自动链不触发研究、组合或交易。

## 文档同步触发

- 数据流、事实源、存储边界变化：更新 `aiDoc/relations/trusted-daily-data-center.md`。
- 数据导入和质量规则变化：更新 `aiDoc/data/`。
- 调度、回填、数据湖边界变化：更新本文件，并按需更新深入架构文档。
- 模拟盘确认/执行接入执行日可交易性、`execution_day_bar_hash` 绑定或 `PaperExecutionService` 政策变化：更新 `aiDoc/plans/hard-gates-execution-plan.md` 与 `aiDoc/plans/roadmap.md` 的 V5.3 小节。
- 组合风险预算（`PortfolioRiskBudget`）、约束与事前风控同源或风险预算违反原因记录变化：更新 `aiDoc/plans/hard-gates-execution-plan.md` 第 4 节与 `aiDoc/plans/roadmap.md` 的 V5.1 小节。
