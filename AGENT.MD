# AGENT.MD

## 目的

本文件是本仓库内 AI 协作规则的唯一真源。

`AGENTS.md` 以及 `.codex/`、`.claude/`、`.cursor/`、`.trae/` 下的规则文件仅作为兼容适配层，不能再次演变成各自独立维护的 project rule 副本。

## 读取顺序

按下面顺序加载项目上下文：

1. `AGENT.MD`
2. `aiDoc/README.md`
3. 按任务读取以下目录中的相关文件：
   - `aiDoc/plans/`
   - `aiDoc/relations/`
   - `aiDoc/data/`
   - `aiDoc/modules/`
   - `aiDoc/research/`
   - `aiDoc/memory/`
4. 仅在当前工具确实依赖时，再读取工具目录下的适配文件

若内容冲突，以 `AGENT.MD` 为准；项目产品、架构、数据、研究和记忆文档统一以 `aiDoc/` 为准。

## 长期协作团队

本项目默认采用长期多角色协作机制。任何支持子代理、智能体或多角色协作的 AI 编程工具，在启动非平凡开发任务时，应优先恢复或模拟以下资深角色，并由主代理汇总裁决：

- 项目经理：持续维护阶段判断、优先级、验收门槛、测试和文档同步节奏，主动推动下一步，不等待用户反复输入“继续”。
- 产品经理：确认产品价值、MVP 边界、用户流程、可验收标准和明确延期项。
- 前端工程师：负责 Vue 前端实现、组件拆分、交互可用性、只读/写入边界表达和构建验证。
- 后端工程师：负责 FastAPI 分层、API/schema、service/repository 边界、测试、幂等、审计和安全写入边界。
- 金融专家：负责量化投资、组合构建、风控、选股与持仓管理边界和核心金融计算正确性。

若当前工具不支持长期子代理，主代理必须按相同角色清单逐项审查并说明降级；不得因为没有运行中的智能体而省略产品、前端、后端、项目管理或金融风控视角。

## 仓库概览

- `backend/`: 主 Python/FastAPI 后端项目
- `backend/app/`: Python/FastAPI 应用代码
- `backend/app/api/`: HTTP API 路由与接口层
- `backend/app/services/`: 业务流程编排，如导入、清洗、校验、修复
- `backend/app/repositories/`: 数据库读写、查询、upsert
- `backend/app/models/`: SQLAlchemy ORM 模型
- `backend/app/database/`: SQLAlchemy Base、Session、数据库连接和 schema 定义
- `backend/app/scheduler/`: 定时任务
- `backend/app/factors/`: 因子计算公共能力
- `backend/app/strategies/`: 策略研究模板
- `backend/app/backtest/`: 日频事件回测引擎
- `backend/app/portfolio/`: 组合构建与持仓目标
- `backend/app/risk/`: 下单前风控
- `frontend/`: Vue3 前端工作台
- `libs/hithink_sdk/`: 独立 HiThink SDK / toolkit 包
- `aiDoc/`: 项目文档、正式设计记录与 AI 协作 reference
- `同花顺数据来源参考/`: 外部数据来源参考资料，不是当前项目实现规范

## 当前项目定位

Stock Master 是 Python 3.13 + FastAPI 的个人量化平台，面向单机单用户采用极简分层存储：DuckDB 单库（`data/stock_master.duckdb`）经 ORM 承载控制面、事务事实与 `raw -> market -> factor -> ... -> trade` 全链路，大体量列式温层落本地 Parquet（见 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`）。唯一例外：手工录入的真实持仓账本（`positions` 域，`position_book`/`position_trade`）存独立 SQLite 文件 `data/positions.db`（决策见 `aiDoc/relations/20261023-1925-position-ledger-sqlite.md`），估值价格仍取 DuckDB `market` 可信层。除此之外经评估不引入 PostgreSQL/SQLite/MinIO/Redis。`backend/app/lake` 温层 Parquet 工具已就绪但主要由离线脚本驱动，接入 `daily_loop` 与 Parquet release 为后续切片。

**项目边界（重要）**：本项目**只用于选股研究与持仓管理**，不连接券商，不做实盘自动化交易，不发送任何真实订单。系统产出止于「研究结论 + 选股名单 + 组合持仓方案 + 风险预算与持仓推演」，由人工在外部终端自行下单。`trade` 域在此语义下仅指本地模拟推演账户，不是通向真实账户的跳板；不引入真实账户只读适配器、外部订单幂等状态机、券商回报对账或 kill switch。

项目已经从 V1 可信日线数据中心推进到 V6 最小运维工作台。当前优先目标是把已经落地的因子、回测、组合、风控和模拟推演能力产品化、可观测化，并继续保持可信数据中心规则：

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

采用引用式读取，避免每次把全部文档读一遍：

- 先读 `aiDoc/README.md` 判断任务涉及的 reference。
- 再只读与当前任务直接相关的 `aiDoc/plans/`、`aiDoc/relations/`、`aiDoc/data/`、`aiDoc/modules/`、`aiDoc/research/` 或 `aiDoc/memory/` 文件。
- 只有当任务触碰对应边界时，才继续读取 `aiDoc/` 下的深入设计文档。
- 不要为了普通小改动重读全部 `aiDoc/`、全部测试或全部源码；优先用 `rg` 定位、读入口文件和受影响文件。

实现数据中心相关能力前，按需阅读：

- 架构和模块边界：`aiDoc/relations/trusted-daily-data-center.md`
- 日线字段：`aiDoc/data/daily-bar-contract.md`
- 数据导入：`aiDoc/data/source-ingestion-policy.md`
- 质量校验与修复：`aiDoc/data/quality-validation-and-repair.md`
- 数据库表结构：`aiDoc/modules/schema-plan.md`

### Vibe Coding 文档同步门槛

代码变更不能只停留在实现层。每次完成任务前必须做一次“文档影响检查”，但只更新确实受影响的文件：

- 改阶段定位、路线、优先级、MVP 边界：更新 `aiDoc/plans/roadmap.md`，必要时更新 `README.md` 和 `aiDoc/plans/README.md`。
- 改架构边界、数据流、存储事实源、外部工具边界：更新 `aiDoc/relations/trusted-daily-data-center.md`，并同步 `aiDoc/relations/system-map.md`。
- 改 schema、ORM、Alembic、索引、约束或表域：更新 `aiDoc/modules/schema-plan.md`，并同步 `aiDoc/modules/module-index.md`。
- 改 API 路由、请求/响应 schema 或前端依赖的接口：更新 `README.md` 的核心 API 面，必要时更新 `aiDoc/README.md`。
- 改数据源、导入、质量规则、修复、血缘或回填策略：更新 `aiDoc/data/` 对应文档。
- 改因子、策略、回测、组合、风控、选股与持仓管理能力：更新 `aiDoc/plans/roadmap.md` 和相关 `aiDoc/research/` 文档。
- 发现长期稳定的项目事实、踩坑经验或用户偏好：更新 `aiDoc/memory/README.md`，不要写进正式产品文档。
- 只做局部 bug fix 且不改变外部行为、架构、schema、API 或产品能力时，不强行改文档；在最终回复说明“文档无影响”。

### 决策评审与提交门

- 任何任务先按 `aiDoc/plans/decision-review-protocol.md` 判定为 T0、T1 或 T2；只有 T2 启动架构师、产品经理、金融行业专家和 Vibe Coding 规则编写者四角色评审。
- T2 包括规则、路线或 MVP、架构边界、公共 API、schema、数据契约与血缘、因子与策略口径、回测假设、组合、风控、持仓管理与核心金融计算变化；项目级 AI 规则变更自身始终按 T2 处理。
- 运行环境支持子代理时，四角色应先独立审查，再由主代理汇总和裁决；不支持时，由主代理按相同清单逐角色审查并明确说明降级，不得省略任何角色。
- AI 执行 `git commit` 前必须检查 staged diff 并重新判级。T2 必须完成项目现状评审、关闭 P0；P1 只有经用户明确接受并记录后才可附条件提交，并给出有证据的优化建议；T0/T1 只做轻量 diff、测试、敏感信息和文档影响检查。
- 评审结论必须包含触发原因、项目现状、证据、风险等级、阻断项、验证结果和文档同步；评审不替代测试，也不得借机扩大当前任务范围。
- 本门禁约束 AI 代办的提交。若需要强制拦截用户或其他工具的手工提交，必须另行建设 Git hook 或 CI，不能声称仅靠文档规则已经实现。

### 代码风格

- 使用 Python 3.13。
- 使用 FastAPI、SQLAlchemy 2.x。
- DuckDB 单库（`data/stock_master.duckdb`）承载控制面、审计、组合、风控、持仓推演等强事务数据与大体量分析事实的查询；大体量列式温层落本地 Parquet。唯一例外是手工记账的真实持仓账本存独立 SQLite（`data/positions.db`，见 `aiDoc/relations/20261023-1925-position-ledger-sqlite.md`）。除此之外不引入 PostgreSQL、SQLite、MinIO 或 Redis（决策见 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`）。
- 数据中心核心价格、金额和比率必须使用精确定点类型；DuckDB 使用 `Numeric`/`DECIMAL`，Parquet 使用 `DECIMAL`，不要用浮点保存核心金融数据。
- 字段命名使用 snake_case。
- 证券代码统一命名为 `symbol`。
- 交易日期统一命名为 `trade_date`。
- 复权类型统一命名为 `adjust_type`。

### AI 文档维护

- `AGENT.MD` 只保留高层、稳定、跨任务的规则。
- 产品、架构、数据、schema、研究和路线设计统一写入 `aiDoc/`。
- AI 协作细节、引用索引、计划、模块地图和轻量记忆也写入 `aiDoc/`，但要按主题分文件，避免入口文件膨胀。
- 若确需生成新的文档文件，文件名必须以前缀时间戳开头，时间从当前系统获取，精确到分钟，格式为 `yyyyMMdd-HHmm-`。
- 长期记忆与业务记忆放在 `aiDoc/memory/`。
- 若项目级 AI 规则发生变化，先改 `AGENT.MD`，再按需更新适配层。
- `aiDoc/README.md` 只做导航，不承载完整设计；深入内容通过 reference 链接读取。

### 历史模板隔离

仓库中可能存在历史模板文档或外部参考资料。凡是描述 Go、Gin、GVA、gin-vue-admin、`server/`、`web/`、`enter.go` 的内容，不作为当前 Stock Master 实现规范，除非文档明确说明它只是历史参考。

## AI 文档索引

- `aiDoc/README.md`
- `aiDoc/plans/README.md`
- `aiDoc/plans/decision-review-protocol.md`
- `aiDoc/plans/roadmap.md`
- `aiDoc/relations/repo-profile.md`
- `aiDoc/relations/system-map.md`
- `aiDoc/relations/trusted-daily-data-center.md`
- `aiDoc/data/daily-bar-contract.md`
- `aiDoc/data/source-ingestion-policy.md`
- `aiDoc/data/quality-validation-and-repair.md`
- `aiDoc/modules/module-index.md`
- `aiDoc/modules/backend-layer-rules.md`
- `aiDoc/modules/schema-plan.md`
- `aiDoc/research/decision-factor-framework.md`
- `aiDoc/memory/README.md`
