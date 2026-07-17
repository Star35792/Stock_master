# Stock Master 文档

Stock Master 当前处于 **V6 可视化和运维** 的最小后端观测阶段，下一步推荐进入 **V7 实盘只读与受控交易**。

项目目标不是简单调用某一个免费接口，而是建立一个本地可维护的数据平台：多源导入、原始留痕、质量校验、人工修复，最终形成可供因子、策略、回测、组合、风控和模拟盘使用的可信数据与执行底座。

## 推荐阅读顺序

1. [V1 可信日线数据中心架构](architecture/trusted-daily-data-center.md)
2. [日线数据字段契约](data/daily-bar-contract.md)
3. [数据源导入策略](data/source-ingestion-policy.md)
4. [数据质量校验与修复设计](data/quality-validation-and-repair.md)
5. [数据库 Schema 规划](database/schema-plan.md)
6. [路线图](roadmap.md)

## 文档分组

```text
docs/
├── architecture/   # 总体架构和模块边界
├── data/           # 数据契约、导入策略、质量校验
├── database/       # schema、表、索引、迁移规划
└── roadmap.md      # 阶段路线图
```

## 当前设计原则

- 任何外部数据源默认不可靠。
- CSV、API、AKShare、QMT、手工补录都必须先进入 `raw`。
- `market` 是可信消费层，供查询、因子、策略和回测使用。
- `factor` 保存可复现的因子定义、运行批次、因子值和横截面统计。
- `strategy` 保存回测运行、成交、净值、持仓和绩效归因。
- `portfolio`、`risk`、`trade` 承接组合构建、下单前风控和模拟盘闭环。
- `system` 记录任务、质量问题和审计信息。
- 人工修复必须留痕，不能静默覆盖。
- 策略和回测不得直接读取外部接口或 `raw` 数据。
- 实盘写操作默认不启用；进入实盘前必须保留人工确认、风控边界和可追溯审计。

## 当前状态

项目已具备从可信行情到模拟盘和运维概览的最小闭环。

已完成：

- 项目基础目录。
- PostgreSQL / Redis docker-compose。
- Python 项目依赖配置。
- 可信数据中心设计文档。
- SQLAlchemy 业务模型和 Alembic 迁移。
- CSV/HiThink 日线导入、参考数据同步和 `raw -> market` 构建入口。
- 质量问题、来源对账、人工修复、缺失检测和查询 API。
- ETF/指数、复权因子、公司行为、交易日历和证券生命周期底座。
- 因子元数据、基础价量因子、横截面处理和统计报告。
- 回测、绩效归因、组合构建和下单前风控。
- 模拟盘账户、订单、成交、现金和持仓闭环。
- `/api/v1/operations/overview` 运维概览接口。

仍需增强：

- 前端看板和图表展示。
- 关键任务告警和细粒度重跑入口。
- 实盘只读同步边界。
- 受控交易前的人工确认、应急开关和审计流程。

## 后续开发约束

实现任何数据中心功能前，应先阅读对应文档：

- 表结构：先读 [数据库 Schema 规划](database/schema-plan.md)。
- 日线字段：先读 [日线数据字段契约](data/daily-bar-contract.md)。
- 数据导入：先读 [数据源导入策略](data/source-ingestion-policy.md)。
- 质量校验和修复：先读 [数据质量校验与修复设计](data/quality-validation-and-repair.md)。
- 模块边界：先读 [V1 可信日线数据中心架构](architecture/trusted-daily-data-center.md)。
- 阶段优先级：先读 [路线图](roadmap.md)。
