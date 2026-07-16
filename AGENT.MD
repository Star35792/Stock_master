# AGENT.MD

## 目的

本文件是本仓库内 AI 协作规则的唯一真源。

`.codex/`、`.claude/`、`.cursor/`、`.trae/` 下的规则文件仅作为兼容适配层，不能再次演变成各自独立维护的 project rule 副本。

## 读取顺序

按下面顺序加载项目上下文：

1. `AGENT.MD`
2. `docs/README.md`
3. `aiDoc/README.md`
4. 按任务读取以下目录中的相关文件：
   - `docs/architecture/`
   - `docs/data/`
   - `docs/database/`
   - `aiDoc/relations/`
   - `aiDoc/modules/`
   - `aiDoc/memory/`
5. 仅在当前工具确实依赖时，再读取工具目录下的适配文件

若内容冲突，以 `AGENT.MD` 为准；正式产品和架构设计以 `docs/` 为准。

## 仓库概览

- `app/`: Python/FastAPI 应用代码
- `app/api/`: HTTP API 路由与接口层
- `app/services/`: 业务流程编排，如导入、清洗、校验、修复
- `app/repositories/`: 数据库读写、查询、upsert
- `app/models/`: SQLAlchemy ORM 模型
- `app/database/`: SQLAlchemy Base、Session、数据库连接和 schema 定义
- `app/scheduler/`: 定时任务
- `app/factors/`: 因子计算，后续阶段启用
- `app/strategies/`: 策略，后续阶段启用
- `app/backtest/`: 回测，后续阶段启用
- `app/portfolio/`: 组合与持仓，后续阶段启用
- `app/risk/`: 风控，后续阶段启用
- `docs/`: 面向人的项目文档与正式设计记录
- `aiDoc/`: 面向 AI 协作的结构化上下文
- `同花顺数据来源参考/`: 外部数据来源参考资料，不是当前项目实现规范

## 当前项目定位

Stock Master 是 Python 3.13 + FastAPI + PostgreSQL + SQLAlchemy + Alembic 的个人量化平台。

当前优先目标是 V1 可信日线数据中心：

- 多源导入。
- 原始数据留痕。
- 质量校验。
- 多源对账。
- 人工修复审计。
- 产出可供策略和回测使用的 `market` 可信层数据。

不要把项目实现为依赖单一 AKShare/Tushare/网页接口的数据脚本。

## 工程规则

### 分层架构

保持以下依赖方向：

```text
api / scripts / scheduler
        ↓
services
        ↓
repositories
        ↓
models / database
```

规则：

- API 层只处理 HTTP 语义、参数校验和响应，不直接写 SQL。
- Service 层负责业务流程，不处理 HTTP 请求对象。
- Repository 层负责数据库读写和查询，不承载业务决策。
- Model 层只定义 ORM 表结构和关系。
- Alembic 负责 schema 变更。
- 外部数据源适配器只负责获取和初步解析数据，不直接写 `market`。

### 数据中心规则

- 任何外部数据源默认不可靠。
- CSV、API、AKShare、QMT、手工补录都必须先进入 `raw`。
- `market` 是可信消费层，供查询、因子、策略和回测使用。
- `system` 记录任务、质量问题、来源配置和修复审计。
- 策略和回测不得直接读取外部接口或 `raw` 数据。
- 人工修复不能静默覆盖，必须写审计记录。

### 文档先行规则

实现数据中心相关能力前，先阅读：

- 架构和模块边界：`docs/architecture/trusted-daily-data-center.md`
- 日线字段：`docs/data/daily-bar-contract.md`
- 数据导入：`docs/data/source-ingestion-policy.md`
- 质量校验与修复：`docs/data/quality-validation-and-repair.md`
- 数据库表结构：`docs/database/schema-plan.md`

### 代码风格

- 使用 Python 3.13。
- 使用 FastAPI、SQLAlchemy 2.x、Alembic。
- 数据库核心价格和金额字段使用 PostgreSQL `numeric`，不要用浮点保存核心金融数据。
- 字段命名使用 snake_case。
- 证券代码统一命名为 `symbol`。
- 交易日期统一命名为 `trade_date`。
- 复权类型统一命名为 `adjust_type`。

### AI 文档维护

- `AGENT.MD` 只保留高层、稳定、跨任务的规则。
- 正式设计写入 `docs/`。
- AI 协作细节写入 `aiDoc/`。
- 长期记忆与业务记忆放在 `aiDoc/memory/`。
- 若项目级 AI 规则发生变化，先改 `AGENT.MD`，再按需更新适配层。

### 历史模板隔离

仓库中可能存在历史模板文档或外部参考资料。凡是描述 Go、Gin、GVA、gin-vue-admin、`server/`、`web/`、`enter.go` 的内容，不作为当前 Stock Master 实现规范，除非文档明确说明它只是历史参考。

## AI 文档索引

- `docs/README.md`
- `docs/architecture/trusted-daily-data-center.md`
- `docs/data/daily-bar-contract.md`
- `docs/data/source-ingestion-policy.md`
- `docs/data/quality-validation-and-repair.md`
- `docs/database/schema-plan.md`
- `docs/roadmap.md`
- `aiDoc/README.md`
- `aiDoc/relations/repo-profile.md`
- `aiDoc/relations/system-map.md`
- `aiDoc/modules/module-index.md`
- `aiDoc/modules/backend-layer-rules.md`
- `aiDoc/memory/README.md`
