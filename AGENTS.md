# Stock Master · AI 协作规则

本文件是本仓库跨模块 AI 协作规则的唯一真源，采用 AGENTS.md 开放格式（https://agents.md）的分层嵌套约定。模块 `AGENTS.md` 只补充本模块规则；`AGENT.MD` 只作旧路径兼容入口。工具配置、会话记忆和专项 Skill 不构成第二份项目规则；新增工具适配文件只引用本文件，不复制规则正文。

## 作用域与读取顺序

1. 先读本文件，再读编辑目标所在目录链上的 `AGENTS.md`（`backend/`、`frontend/`、`scripts/`、`libs/`）。模块规则补充本文件；同一事项冲突时以本文件的跨模块约束为准。
2. 再读 `aiDoc/README.md` 的 Reference Map，按任务类型只打开直接相关的 `aiDoc/` 文档。不要为普通改动全量重读文档、测试或源码；优先 `rg` 定位，再读入口文件和受影响文件。
3. 用户当前的明确指令优先于本文件中的所有规则。

## 项目边界（硬约束）

- Stock Master 是 Python 3.13 + FastAPI 的单机单用户个人量化平台，**只做选股研究与持仓管理**：不连接券商，不做实盘自动化交易，不发送任何真实订单。产出止于研究结论、选股名单、组合持仓方案、风险预算与持仓推演，由人工在外部终端自行下单。
- `trade` 域只表示本地模拟推演账户，不是通向真实账户的跳板；不引入真实账户只读适配器、外部订单幂等状态机、券商回报对账或 kill switch。
- 存储事实源（四个库都在 `backend/data/` 下）：主库 `backend/data/stock_master.duckdb` 承载业务控制面、事务事实与温层 release 链；研究库 `backend/data/research.duckdb` 承载 strategy 回测产物；人工记录的真实持仓账本 `backend/data/positions.db` 与智能体状态库 `backend/data/agent.db` 使用 SQLite。智能体状态库只保存会话、计划、确认检查点和用户明确保存的长期记忆；业务任务结果以原任务审计和研究产物为准，只保留有界摘要与来源 ID，不复制整份行情历史、持仓流水或研究产物作为另一事实源。大体量列式历史落本地 Parquet 温层。除此之外不引入 PostgreSQL、MinIO 或 Redis。
- 后端应用、数据维护和研究脚本必须以 `backend/` 为工作目录。库路径、`data_lake_root` 与 `.env` 按当前工作目录解析；从仓库根运行后端代码会静默另建一份空库、另一把写锁并跳过 `backend/.env`。仓库级 `scripts/ci_check.py` 会自行设置各步骤的工作目录，可从仓库根调用；前端命令按 `frontend/AGENTS.md` 执行。
- 阶段状态与近期优先级以 `aiDoc/plans/roadmap.md` 为准。保持可信数据中心规则，不把项目实现为依赖单一 AKShare、Tushare 或网页接口的数据脚本。

## 工程规则

### 分层架构

依赖方向固定为 `api / scripts / scheduler → services → repositories → models / database`。

- API 层只处理 HTTP 语义、参数校验与响应，不直接写 SQL；Service 层编排业务流程，不处理 HTTP 请求对象；Repository 层负责数据库读写与查询，不承载业务决策；Model 层只定义 ORM 表结构与关系。
- 外部数据源适配器只负责获取和初步解析数据，不直接写 `market`。
- 当前 schema 由各库 ORM 初始化、主库显式兼容桥与专项维护脚本维护，尚无完整版本化迁移与回退体系；schema 变更仍须经过既定评审、幂等升级与验证，不得把启动初始化当作任意改库授权。

### 数据中心

- 任何外部数据源默认不可靠。CSV、API、AKShare、QMT、手工补录都必须先进 `raw` 留痕。
- `market` 是唯一可信消费层，供查询、因子、策略和回测使用；策略和回测不得直接读取外部接口或 `raw`。
- `system` 域记录任务、质量问题、来源配置和修复审计；人工修复不能静默覆盖，必须写审计记录。

### 存储容量与单份数据

- 本机每项必要事实只保留一份权威数据：不得自动创建全库备份、长期 `.backup-*` 副本或无限增长的临时产物。主库、研究库、持仓账本、智能体状态库与有效温层 release 链各有职责，不互为备份。
- 调试、测试、采集、修复、导出前优先只读查询、内存处理与摘要输出。确需落盘时先明确唯一用途、受控目录、大小或数量上界、事实源/引用与清理时机；同来源、同业务键、同内容的重复采集须幂等，真实变化的原始版本仍须留痕。
- 每次任务收尾盘点本轮新增和变大的数据、测试与临时文件：成功和失败路径都要清理自身生成且无引用的可再生产物，并保留必要的审计摘要、哈希与错误记录。不能证明可删的来源证据、WAL、有效温层 release、研究引用不适用自动清理规则。
- 清理前核对 active 指针、温层父链 snapshot/delta 语义、研究/审计引用和必要 raw 血缘，并验证行数、manifest 或校验值；不得仅凭文件名、时间或体积删除。执行环境拒绝删除时报告阻断，不用改名、脚本包装或其他工具绕过。

### 代码风格

- 使用 Python 3.13、FastAPI、SQLAlchemy 2.x。字段命名 snake_case；证券代码统一 `symbol`、交易日期统一 `trade_date`、复权类型统一 `adjust_type`。
- 核心价格、金额和比率必须使用精确定点类型：DuckDB 用 `Numeric`/`DECIMAL`，Parquet 用 `DECIMAL`，不得用浮点保存核心金融数据。
- 同一类可机械识别的错误反复出现时，优先改进代码结构、类型约束、lint、测试或 CI；文字规则和 Skill 主要承载仍需判断的事项，不堆叠同义提醒。

## 验证入口

- 本地与 CI 共用同一入口。从仓库根运行 `uv run --project backend python scripts/ci_check.py --scope all`（也可选择 `backend` 或 `frontend`）；脚本自行进入对应模块目录，按步失败即停并保留退出码。Windows 上不要用裸 `python`（可能是商店别名，只会静默失败）。
- 单模块命令与门写在该模块自己的 `AGENTS.md`；改公共门本身必须同步更新契约测试 `backend/tests/test_ci_check_runner.py`。

## 协作与提交门

### 长期协作团队

非平凡开发任务由主代理按影响范围统筹项目经理、产品经理、金融专家、后端工程师和前端工程师视角：项目经理定优先级和验收节奏；产品经理定用户流程与 MVP；金融专家核对数据时点、研究口径和风险边界；后端工程师核对分层、幂等与审计；前端工程师核对交互和构建。T0 只做常规检查，T1 只核对受影响的视角；T2 按评审协议中的风险触发条件选择独立复核角色，重合结论可直接复用。无独立复核条件时，由主代理自查并如实标明，不把分角色自查称为独立评审。

### 决策评审与提交

- 拟修改代码、规则或文档时，先按 `aiDoc/plans/decision-review-protocol.md` 判定 T0、T1 或 T2；纯只读问答无需变更分级。T2 是影响分级，不自动要求固定人数；独立复核按实际风险和受影响边界触发。
- T2 包括规则、路线或 MVP、架构边界、公共 API、schema、数据契约与血缘、因子与策略口径、回测假设、组合、风控、持仓管理和核心金融计算变化；**项目级 AI 规则变更自身始终按 T2 处理**。
- 执行 `git commit` 前必须检查 staged diff 并重新判级：T2 必须完成项目现状评审并关闭 P0；P1 只有经用户明确接受并记录后才可附条件提交，并给出有证据的优化建议。T0/T1 只做轻量 diff、测试、敏感信息和文档影响检查。
- 评审结论必须包含触发原因、项目现状、证据、风险等级、阻断项、验证结果和文档同步；评审不替代测试，也不得借机扩大当前任务范围。
- AI 代办的提交标题、正文以及类型和范围前缀默认全部使用中文，不使用 `chore`、`fix` 等英文前缀；用户明确指定其他语言时以用户要求为准。
- 本门禁约束 AI 代办的提交。若要强制拦截用户或其他工具的手工提交，必须另行建设 Git hook 或 CI，不得声称仅靠文档规则已经实现。

### 文档同步门槛

每次完成任务前做一次"文档影响检查"，只更新确实受影响的文件：

| 变更内容 | 必须同步 |
| --- | --- |
| 阶段定位、路线、优先级、MVP 边界 | `aiDoc/plans/roadmap.md`，必要时 `README.md` 和 `aiDoc/plans/README.md` |
| 架构边界、数据流、存储事实源、外部工具边界 | `aiDoc/relations/trusted-daily-data-center.md`，并同步 `aiDoc/relations/system-map.md` |
| schema、ORM、索引、约束或表域 | `aiDoc/modules/schema-plan.md`，并同步 `aiDoc/modules/module-index.md` |
| API 路由、请求/响应 schema 或前端依赖的接口 | `README.md` 的核心 API 面，必要时 `aiDoc/README.md` |
| 数据源、导入、质量规则、修复、血缘或回填策略 | `aiDoc/data/` 对应文档 |
| 因子、策略、回测、组合、风控、选股与持仓管理 | `aiDoc/plans/roadmap.md` 和相关 `aiDoc/research/` 文档 |
| 长期稳定的项目事实、踩坑经验或用户偏好 | `aiDoc/memory/README.md`，不要写进正式产品文档 |
| 新决策推翻或改写既有时间戳文档的事实 | 新写一份 `yyyyMMdd-HHmm-` 文档，并把被取代文档的 `status` / `superseded_by` / `note` 改掉，再运行 `scripts/doc_status_index.py` 重新生成索引 |
| 局部 bug fix 且不改变外部行为、架构、schema、API 或产品能力 | 不强行改文档，在最终回复说明"文档无影响" |

## 文档维护

- 本文件只保留高层、稳定、跨任务的规则；模块级命令与边界写进模块 `AGENTS.md`；产品、架构、数据、schema、研究和路线设计统一写入 `aiDoc/`，并按主题分文件，避免入口文件膨胀。
- `aiDoc/README.md` 只做导航，不承载完整设计；深入内容通过 Reference Map 按需读取。
- 需要新建文档文件时，文件名必须以前缀时间戳开头，时间从当前系统获取、精确到分钟，格式为 `yyyyMMdd-HHmm-`。已存在的两份 `20261019-low-price-rebound-backtest.md`、`20261026-valuation-history-backfill.md` 是缺分钟的历史例外，只保留不改名（改名会让既有引用失效）。
- 文档分两层：无时间戳的 living docs（`roadmap.md`、`system-map.md`、`module-index.md`、`schema-plan.md`、`data/daily-bar-contract.md`、`memory/README.md` 等）承载**当前状态**，随代码回写；`yyyyMMdd-HHmm-` 前缀文档承载**决策与证据的历史层**，落定后不再回改正文事实。
- 每份时间戳文档头部必须有 YAML frontmatter：`doc_type`（`decision`/`plan`/`evidence`/`analysis`/`config`/`index`）、`status`（`current`/`snapshot`/`partially_superseded`/`withdrawn`）、`as_of`（口径最后核实日），以及按需 `superseded_by`、`note`。`superseded_by` 的路径**相对 `aiDoc/`**，`../` 开头表示仓库根方向。引用前先查状态，不用整篇重写来标注陈旧。
- [文档状态索引](aiDoc/20261115-1649-doc-status-index.md) 由 `uv run --project backend python scripts/doc_status_index.py` 从各文档 frontmatter 生成，不手工编辑；新增时间戳文档、改 `status`/`as_of`/`superseded_by`/`note` 或移动文件后必须重新运行，并用 `--check` 确认无漂移。`backend/tests/test_doc_status_index.py` 会挡住缺 frontmatter、悬空取代引用和索引未再生成三种违约。状态真源是各文档自己的 frontmatter，索引只做聚合。
- 开发协作的长期事实与业务经验放在 `aiDoc/memory/`；产品内由用户明确保存的智能体记忆放在 `backend/data/agent.db`，两者不互作事实源。项目级 AI 规则变化时先改本文件，再按需更新模块 `AGENTS.md` 与适配层。

## 历史模板与外部参考隔离

- 凡是描述 Go、Gin、GVA、gin-vue-admin、`server/`、`web/`、`enter.go` 的内容，不作为当前 Stock Master 实现规范，除非文档明确说明它只是历史参考。
- 未纳入版本管理的顶层目录（如 `OSkhQuant-main/`、`a-stock-data-main/`、`同花顺数据来源参考/`）是外部下载参考，不是本项目实现规范；`libs/hithink_sdk/` 是上游 SDK 副本，其文档口径不代表本仓库实现。
