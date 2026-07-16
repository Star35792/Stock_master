# Stock Master 文档

Stock Master 当前处于 **V1 可信日线数据中心** 设计和建设阶段。

项目目标不是简单调用某一个免费接口，而是建立一个本地可维护的数据平台：多源导入、原始留痕、质量校验、人工修复、最终形成可供策略和回测使用的可信行情数据。

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
- `system` 记录任务、质量问题和审计信息。
- 人工修复必须留痕，不能静默覆盖。
- 策略和回测不得直接读取外部接口或 `raw` 数据。

## 当前状态

项目已有 Python/FastAPI/PostgreSQL 基础骨架，但 V1 数据中心仍处于设计和早期实现前阶段。

已完成或正在整理：

- 项目基础目录。
- PostgreSQL / Redis docker-compose。
- Python 项目依赖配置。
- 可信数据中心设计文档。

尚未完成：

- SQLAlchemy 业务模型。
- Alembic 初始迁移。
- CSV 导入器。
- 数据质量校验实现。
- market 可信数据生成流程。
- 查询 API。

## 后续开发约束

实现任何数据中心功能前，应先阅读对应文档：

- 表结构：先读 [数据库 Schema 规划](database/schema-plan.md)。
- 日线字段：先读 [日线数据字段契约](data/daily-bar-contract.md)。
- 数据导入：先读 [数据源导入策略](data/source-ingestion-policy.md)。
- 质量校验和修复：先读 [数据质量校验与修复设计](data/quality-validation-and-repair.md)。
- 模块边界：先读 [V1 可信日线数据中心架构](architecture/trusted-daily-data-center.md)。
