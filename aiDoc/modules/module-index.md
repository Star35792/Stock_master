# 模块索引 Reference

## 后端模块

| 模块 | 当前职责 |
| --- | --- |
| `api` | HTTP 语义、参数校验、响应模型 |
| `services` | 导入、构建、因子、批量因子截面诊断、回测、组合、风控、模拟盘等业务流程 |
| `factors` | 因子分类常量与因子定义目录预注册（registry） |
| `repositories` | SQLAlchemy 查询、upsert、分页、持久化 |
| `models` | ORM 表结构 |
| `data_sources` | HiThink、Eastmoney、Xueqiu 等外部来源适配 |
| `scheduler` | 工作日同步、近期补齐、因子批量计算日级调度，以及 `daily_loop` 数据→因子闭环编排；所有调度入口写入 `system.job_log` |
| `lake` | Parquet/DuckDB 离线数据湖 |
| `backtest` | 日频事件回测内核 |
| `strategies` | 策略研究模板 |
| `strategies.price_value` / `services.price_value_research` | 纯 Decimal FCFE 情景计算及只读研究编排；消费可信行情与显式用户假设，不认证历史财务、不生成交易信号 |
| `backtest.price_value` / `services.price_value_backtest` | 单股加现金的版本化假设历史模拟；复用 FCFE 与费用计算，可信未复权行情及交易日历驱动收盘信号、次日开盘成交和持续退出；仅保存 `strategy.backtest_*` 研究事实与合同，不创建 paper/真实订单、不认证历史时点 |
| `portfolio` | 组合构建内核；`portfolio/defaults.py` 是风险预算默认值的唯一来源（可由 settings 覆盖） |
| `market` | `price_limit.py` 按板块从 `pre_close` 推导涨跌停价；`utils/symbol.py` 提供 symbol→交易所解析 |
| `repositories.research_read` | 温层 Parquet 只读仓库（ORM 仓库的孪生实现），供回测/研究读路径使用 |
| `risk` | 下单前风控内核 |
| `trade` | 本地模拟推演执行与持仓记录（BrokerAdapter 仅隔离本地模拟回报） |
| `services.position_book` / `services.position_metrics` / `services.position_analysis` / `services.portfolio_drift` | 真实持仓账本（独立 SQLite）记账与重放推导、共享 Decimal 持仓指标（盈亏/集中度/行业暴露）、paper 账户持仓与净值绩效只读分析、组合草案与真实/模拟持仓的偏离检测（只读建议，不生成订单） |

当前公共边界：`api` 已暴露行情、数据质量、因子、回测、策略研究、运维查询，以及组合草案、只读风险前数据核对、paper 账本一致性审计、paper 账户不可变净值快照、绑定服务端事实源的正式风险评估、短时人工确认和幂等 paper 执行批次。账本审计只读取既有订单、成交、执行批次、现金和持仓事实并报告差异，不自动修复。执行入口只消费确认记录，重新验证全部哈希并固定使用本地 PaperBrokerAdapter；本项目不接入任何真实账户，也不实现受控交易适配器或实盘自动化交易。

## Schema 域

- `raw`：导入批次、原始行情、原始财务、原始公司行为、vendor snapshot。
- `market`：证券、交易日历、可信日线、复权因子、公司行为、另类数据、估值快照。证券名称做包含匹配，旧 DuckDB 名称索引由启动兼容升级移除；研究估值观察值通过批次标记与通用估值因子隔离。
- `fundamental`：财务报表和指标。
- `factor`：因子定义、运行、值、profile。
- `strategy`：回测运行、成交、净值、持仓、绩效，以及训练产物关联；被回测引用的训练产物由外键阻止删除，不静默置空血缘。
- `portfolio`：组合计划和目标。
- `risk`：旧版风控检查，以及绑定 plan/account/equity/readiness/ADV/policy 哈希的正式风险评估和逐订单规则结果。
- `trade`：本地模拟推演账户、订单、成交、持仓，append-only 的账户净值快照与逐持仓估值组件，短时人工确认记录，以及 approval 唯一绑定的原子执行批次。
- `positions`（独立 SQLite `data/positions.db`）：手工记账的真实持仓账本（`position_book`/`position_trade`）；持仓与盈亏由流水重放推导，估值价格取 DuckDB `market` 可信层。
- `lake`：内容寻址的数据集 release、父版本、checksum、质量状态和每个 dataset 的 active 指针。
- `system`：任务日志（`job_log`）、质量问题、数据源配置；`daily_sync`、`factor_pipeline`、`factor_batch_profiler` 和 `daily_loop` 每次运行均留下 running → success/failed/partial 记录。

## 同步规则

- 新增/改动 schema 域：更新 `aiDoc/modules/schema-plan.md`。
- 新增 API：更新 `README.md` 核心 API 面。
- 新增模块或改变职责：更新本文件和 `AGENT.MD` 仓库概览。
