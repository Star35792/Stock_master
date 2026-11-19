# 模块索引 Reference

## 后端模块

| 模块 | 当前职责 |
| --- | --- |
| `api` | HTTP 语义、参数校验、响应模型 |
| `services.factor_recovery` / `repositories.factor.recovery` | 从原记录恢复有界单因子计算，预检输入与覆盖，新 run 留真实代码和价格摘要并复查；未知版本拒绝，旧失败保留 |
| `scripts.maintenance.repair_index_consistency` | 默认只读比较索引点查与全表键；精确指定已证实异常索引后持锁维护，唯一 guard、前后全列哈希及 checkpoint 重开验证，写审计，不复制业务库 |
| `services` | 导入、构建、因子、批量因子截面诊断（含证据资格汇总）、回测、组合、风控、模拟盘等业务流程 |
| `factors` | 因子分类常量与因子定义目录预注册（registry） |
| `repositories` | SQLAlchemy 查询、upsert、分页、持久化 |
| `models` | ORM 表结构 |
| `data_sources` | HiThink、Eastmoney、Xueqiu、Tencent 行情/估值、TDX 官方行情（经 `libs/a_stock_data` 净化加载）、申万行业、基金与证据采集（公司行为/退市/换手率）适配；只做获取与初步解析，不直接写 `market` |
| `schemas` | 请求/响应 Pydantic 合同，含费用模型版本白名单等取值约束 |
| `config` | `settings.py`（pydantic-settings，读 `backend/.env`，含 `source_code_version()` 代码摘要）与 `data_contracts.py`（跨层数据合同版本常量，如复权因子可信来源） |
| `indicators` | 指标计算公共能力，供因子与策略复用 |
| `utils` | 跨层工具：`duckdb_write_lock.py`（域写锁）、`discard.py`（安全废弃）、`symbol.py` 等 |
| `services.job_lease` | 任务进程 OS 租约（每进程占 32 个固定槽之一）与孤儿 `running` 收敛：只有对应锁可取得时才写 `failed + completion_status=unknown`；旧版无租约记录由 `scripts.maintenance.reconcile_legacy_running_jobs` 显式收敛 |
| `scheduler` | 工作日同步、近期补齐、因子批量计算日级调度，以及 `daily_loop` 数据→因子闭环编排；所有调度入口写入 `system.job_log`。随 API 进程只启动 3 个 worker（`auto_data_sync`、`price_value_input_prep`、`price_value_screen`，见 `app/main.py` lifespan，因此服务必须单 worker 运行）；`create_daily_loop_scheduler` / `create_daily_sync_scheduler` / `create_factor_pipeline_scheduler` 是 cron 工厂但仓内无调用方，须显式用 `backend/scripts/scheduler/` 下对应脚本运行，当前只有 `create_recent_data_completion_scheduler` 有仓内入口 |
| `lake` | Parquet/DuckDB 离线数据湖 |
| `backtest` | 日频事件回测内核 |
| `services.backtest` / `services.backtest_query` / `services.backtest_snapshot` | 回测执行编排、结果查询与比较、确定性输入快照分别维护；`services.backtest.BacktestQueryService` 保留兼容导入，快照字段、版本及哈希序列化口径不变 |
| `strategies` | 策略研究模板 |
| `strategies.quality_value_attention` / `services.quality_value_data` / `repositories.strategy.quality_value_data` | 双低一高只读研究筛选：显式证据、硬门槛、归因/催化剂、六维评分；证据包CLI和数据库只读CLI，后者保留全股票池及数据缺口，无收益回测、仓位或交易信号 |
| `strategies.price_value` / `services.price_value_research` | 纯 Decimal FCFE 情景计算及只读研究编排；消费可信行情与显式用户假设，不认证历史财务、不生成交易信号 |
| `strategies.value_investing` / `services.value_investing_screen` | 内置价值投资选股内核（`value_investing_margin_v3`）：准入 → 质量 → 价值 → 安全边际四层漏斗 + **企业质地闸门**（3 年年化 ROE 下限）+ **价值陷阱闸门**（4 年年报窗口的营收/净利连跌年数、负债率 3 年变化）+ **成长路径**（营收/净利 3 年 CAGR，可替代 ROE 闸门但绝不替代价格闸门，陷阱闸门优先于成长路径）；估值锚（历史倍数保守锚，非内在价值）用盈利锚与净资产锚**取较小者**，`buy_cost_ceiling = V_low × (1 - required_margin)`；纯 Decimal、无 I/O、确定性排序，缺失证据阻断、已知不合格排除；报告输出 `quality_profile`（企业研究维度覆盖状态，含「是不是价值陷阱」硬闸门维度）、`price_gate_only`（关闭质地闸门的对照，代价可见）、`growth_path_count` 与 `value_trap_removed_count`；**DCF 不进闸门**，绝对估值仍由只读 FCFE 情景研究承担 |
| `services.value_investing_research_card` / `models.strategy.external_observation` | 外部连接器观测的**研究卡**与可审计快照表（研究库 `strategy.external_observation`）：补上本地无数据的「主营业务」与「市场份额/竞争优势」两维，与已实现事实分区呈现（一致预期放 `forward_looking` 块并附警示语）；**外部数据一律不参与任何闸门判定**——无时点、前瞻性预测接入闸门会让 PIT 约束形同虚设；没有观测的证券不出卡 |
| `strategies.registry` / `repositories.strategy.definition` / `models.strategy.definition` | 内置策略定义目录 `strategy.strategy_definition`（主库）：`name`+`version` 唯一键、`parameters_schema` 参数契约、`parameters` 锁定阈值取值、`locked_parameters` 锁定清单、`required_margin` 锁定安全边际（`NUMERIC(12,10)`）；按 `(name, version)` 幂等 upsert（`@v1`/`@v2` retired、`@v3` active），屏幕从目录读回锁定参数而非从调用方取值 |
| `backtest.price_value` / `services.price_value_backtest` | 单股加现金的版本化假设历史模拟；复用 FCFE 与费用计算，可信未复权行情及交易日历驱动收盘信号、次日开盘成交和持续退出；仅保存 `strategy.backtest_*` 研究事实与合同，不创建 paper/真实订单、不认证历史时点 |
| `portfolio` | 组合构建内核；`portfolio/defaults.py` 是风险预算默认值的唯一来源（可由 settings 覆盖） |
| `market` | `price_limit.py` 按板块从 `pre_close` 推导涨跌停价；`utils/symbol.py` 提供 symbol→交易所解析 |
| `repositories.research_read` | 温层 Parquet 只读仓库（ORM 仓库的孪生实现），供回测/研究读路径使用；含 `list_valuation_history` / `valuation_history_coverage` 读温层研究表 `research.valuation_history` |
| `repositories.research_valuation` | 估值历史 ORM 孪生读取仓库，读控制面 `market.valuation_snapshot`；排除 `_research_batch_id` 研究观察，列表与覆盖统计同口径，`pb_mrq` 报 `None`、`pit_verified` 默认 `false`；两条研究读取路径保留 `source_name` 并按日期、证券、来源确定性排序 |
| `market.special_treatment` | 从证券简称派生 ST / *ST / 退市整理状态（交易所改名即状态证据）；只对 `security_type='stock'` 生效，概念/行业指数名称含「ST」字样不误判 |
| `services.calendar_completion` / `repositories.market.calendar_observation` | 核验成功参考数据批次的完整原始开市日列表、页哈希与三所映射；仅在源首末日期内部补齐自然日日历，拒绝已有状态冲突，保留派生批次审计 |
| `services.price_value_input_prep` / `services.price_value_screen` | 晚间或显式当前补采的相对估值研究观察；区分价格日与实际财务可得时刻，校验血缘、鲜度、走势与现金流证据，保存版本化名单；研究观察排除通用估值及历史认证消费 |
| `services.price_value_shortlist` | 纯 Decimal 的版本化估值与连续盈利门槛、周期行业及异常 ROE 复核分流；确定性排序后生成最多10只且每行业最多2只的人工研究队列，保留全量达标池、容量递延与逐股原因 |
| `services.valuation_history_collection` | 东财估值历史采集服务：并发分页、Decimal 精度保持、双闸门（价格零容差 + 股票池覆盖下限 + 重复键）、SQL 内 JOIN 闸门、原子发布 |
| `utils.discard` | 先将废弃目录改名移出读取路径，再尝试回收当前及既有 `.trash/` 目录；清理失败会留痕，不能把 `.trash/` 当长期保留层 |
| `risk` | 下单前风控内核 |
| `trade` | 本地模拟推演执行与持仓记录（BrokerAdapter 仅隔离本地模拟回报） |
| `services.position_book` / `services.position_metrics` / `services.position_analysis` / `services.portfolio_drift` | 真实持仓账本（独立 SQLite）记账与重放推导、共享 Decimal 持仓指标（盈亏/集中度/行业暴露）、paper 账户持仓与净值绩效只读分析、组合草案与真实/模拟持仓的偏离检测（只读建议，不生成订单） |
| `database.research_session` / `backtest.rebalance` / `utils.duckdb_write_lock` | 独立 research.duckdb 引擎与会话（strategy 产物域，`research_db=None` 兼容贯穿）、回测调仓日选择（daily/weekly/monthly/N 交易日）、DuckDB 跨进程写锁（主库 `duckdb_write.lock` / 研究库 `research_write.lock`） |
| `database.session` / `scripts.maintenance.recover_duckdb_wal` | 控制面 schema 兼容桥：为旧组合计划/目标表幂等补齐 nullable `style_risk` / `score`；序列默认值仅在缺失或指向错误序列时才补发 DDL，初始化末尾立即 CHECKPOINT，避免 WAL 留下不可回放的默认表达式条目（DuckDB #18259）；`recover_duckdb_wal` 用 ATTACH 绕过该回放缺陷并无损折回 WAL |
| `scripts.maintenance.compact_control_plane` | 控制面空闲空间回收：CTAS 重写主库文件，回收 DuckDB 删除行后未归还的空闲块（数据行完全不变），并恢复 CTAS 丢失的 40 个序列、唯一索引与列默认值；发布前逐表比对行数，不一致即拒绝发布 |

当前公共边界：`api` 已暴露行情、数据质量、因子、回测、策略研究、运维查询，以及组合草案、只读风险前数据核对、paper 账本一致性审计、paper 账户不可变净值快照、绑定服务端事实源的正式风险评估、短时人工确认和幂等 paper 执行批次。账本审计只读取既有订单、成交、执行批次、现金和持仓事实并报告差异，不自动修复。执行入口只消费确认记录，重新验证全部哈希并固定使用本地 PaperBrokerAdapter；本项目不接入任何真实账户，也不实现受控交易适配器或实盘自动化交易。

## Schema 域

- `raw`：导入批次、原始行情、原始财务、原始公司行为、vendor snapshot。
- `market`：证券、交易日历、可信日线、复权因子、公司行为、另类数据、估值快照。证券名称做包含匹配，旧 DuckDB 名称索引由启动兼容升级移除；研究估值观察值通过批次标记与通用估值因子隔离。
- `fundamental`：财务报表和指标。
- `factor`：因子定义、运行、值、profile；profile 的证据资格独立于定义 PIT 状态，默认仅研究用途，核心池还要求明确的 `strategy_eligible` 资格。
- `strategy`：内置策略定义目录 `strategy_definition`（主库，配置型定义，非运行产物）+ 回测运行、成交、净值、持仓、绩效，以及训练产物关联（后 6 张表在独立研究库）；另含外部连接器观测快照 `external_observation`（研究库，**永不被闸门读取**）。被回测引用的训练产物由外键阻止删除，不静默置空血缘。
- `research`：目前只存在于温层 Parquet，**不在控制面**。`research.valuation_history` 是东财估值历史序列，全部行 `pit_verified=false`，`pb_mrq` 按报告期末切换、最多前视 120 天因而不可作为可交易证据。判定标准与现有表清单见 `aiDoc/modules/schema-plan.md` §17。
- `portfolio`：组合计划和目标；旧主库的可选风格风险 JSON 与目标分数定点列由兼容桥补齐，旧值保留空值。
- `risk`：旧版风控检查，以及绑定 plan/account/equity/readiness/ADV/policy 哈希的正式风险评估和逐订单规则结果。
- `trade`：本地模拟推演账户、订单、成交、持仓，append-only 的账户净值快照与逐持仓估值组件，短时人工确认记录，以及 approval 唯一绑定的原子执行批次。
- `positions`（独立 SQLite `backend/data/positions.db`）：手工记账的真实持仓账本（`position_book`/`position_trade`）；持仓与盈亏由流水重放推导，估值价格取 DuckDB `market` 可信层。
- `lake`：内容与不可变血缘共同寻址的数据集 release、父版本、checksum、质量状态和每个 dataset 的 active 指针。
- `system`（业务模块名，物理 schema 为 `metadata`）：任务日志（`job_log`）、质量问题、数据源配置；`daily_sync`、`factor_pipeline`、`factor_batch_profiler` 和 `daily_loop` 每次运行均留下 running → success/failed/partial 记录，新 running 必须绑定进程租约，孤儿 running 只按 OS 锁证据收敛。

## 同步规则

智能体第一阶段（2026-11-19）：`database/agent_base.py` / `agent_session.py` 与 `models/agent.py` 定义独立 SQLite `agent.db`，`repositories/agent.py` 负责有界会话与记忆的读写和 CAS，`services/agent_state.py` 负责短事务，`services/agent.py` 保存/恢复对话和确认检查点。`deepseek.py` 仅模型传输，`agent_tools.py` 适配既有 service。前端 `/agent` 可恢复会话和显式管理记忆。写执行默认关闭；完整范围以[整体计划](../plans/20261119-1049-agent-overall-plan-and-sqlite.md)为准。

- 新增/改动 schema 域：更新 `aiDoc/modules/schema-plan.md`。
- 新增 API：更新 `README.md` 核心 API 面。
- 新增模块或改变职责：更新本文件和对应模块的 `AGENTS.md`（根 `AGENTS.md` 只保留跨模块规则，不再维护仓库概览清单）。
