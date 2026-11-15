# AI 协作与项目文档入口

本目录是 Stock Master 的统一项目文档层：既保存产品、架构、数据、schema、研究和路线设计，也保存 vibe coding 所需的轻量 reference。

## 使用规则

1. 先读根 `AGENTS.md`，再读编辑目标所在目录链上的模块 `AGENTS.md`（模块清单以根 `AGENTS.md` 的「作用域与读取顺序」为准，不在本文件复制），最后读本文件。
2. 根据任务类型只读下方相关 reference。
3. 只有任务触碰对应边界时，才继续打开深入设计文档。
4. 引用任何带时间戳前缀的文档前，先看它头部的 YAML `status`；批量判断口径新旧读[文档状态索引](20261115-1649-doc-status-index.md)。无时间戳前缀的 living docs 才承载当前状态。
5. 完成代码任务前做一次文档影响检查：能力、API、schema、架构、路线、数据规则或长期记忆发生变化，就同步对应文档。

## 长期协作机制

长期协作团队的唯一规则真源在根 `AGENTS.md` 的“长期协作团队”小节。进入非平凡开发任务时，主代理应按项目经理、产品经理、前端工程师、后端工程师、金融专家五个资深角色推进；能启动子代理就恢复对应角色，不能启动时由主代理逐角色审查并明确降级。

默认推进顺序：项目经理定优先级，产品经理定价值和验收，金融专家定风控与交易边界，后端工程师定 API、审计和幂等，前端工程师定操作流和工程结构，最后由主代理整合实现、验证并做文档影响检查。

## Reference Map

| 任务类型 | 先读 reference | 必要时再读深入文档 |
| --- | --- | --- |
| 重大决策、AI 提交前评审 | `aiDoc/plans/decision-review-protocol.md` | 按受影响边界读取下方对应文档 |
| 阶段计划、路线、MVP | `aiDoc/plans/README.md` | `aiDoc/plans/roadmap.md` |
| 架构、数据流、存储边界 | `aiDoc/relations/system-map.md` | `aiDoc/relations/trusted-daily-data-center.md` |
| 本机单份数据保留、冗余清理与容量验收 | [本机存储保留与清理交接](relations/20261112-1341-local-storage-retention-and-cleanup.md) | [历史存储诊断](relations/20261013-2207-storage-diagnosis-and-plan.md)、`aiDoc/relations/system-map.md` |
| 真实持仓账本存储（SQLite 例外）、持仓分析与偏离检测 | [持仓账本 SQLite ADR](relations/20261023-1925-position-ledger-sqlite.md) | `aiDoc/plans/roadmap.md` V5.4、`aiDoc/modules/schema-plan.md` |
| 研究库拆分（research.duckdb）、DuckDB 跨进程并发 | [研究库拆分 ADR](relations/20261024-1427-research-database-split.md) | `aiDoc/relations/system-map.md`、`aiDoc/modules/schema-plan.md` |
| 主库打不开、WAL 回放失败、DuckDB 存储可用性 | [不可回放 WAL 的无损恢复与预防](relations/20261106-1442-duckdb-unreplayable-wal-recovery.md) | `aiDoc/memory/README.md`、`aiDoc/modules/module-index.md` |
| 控制面空闲空间回收、索引与序列恢复 | [控制面主库空闲空间回收与索引/序列恢复](relations/20261109-1648-control-plane-compaction-and-index-recovery.md) | `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`、`aiDoc/memory/README.md` |
| 外部数据源接入评估、跨源对账、多源一致性 | [a-stock-data 接入评估](relations/20261111-1415-a-stock-data-integration-assessment.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/data/quality-validation-and-repair.md` |
| 多源对账激活、数据源注册表、独立第二源接入 | [多源对账激活与独立第二源接入](relations/20261111-1458-multi-source-reconciliation-activation.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/relations/trusted-daily-data-center.md` |
| 估值第二独立源、TTM PE 补齐、跨源估值对账口径 | [腾讯行情接入：估值的第二独立源与 TTM PE 补齐](relations/20261111-1531-tencent-valuation-source.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/data/quality-validation-and-repair.md` |
| 后端分层、代码规则 | `aiDoc/modules/backend-layer-rules.md` | 相关 `backend/app/**` 文件 |
| schema、表、迁移 | `aiDoc/modules/module-index.md` | `aiDoc/modules/schema-plan.md`、`backend/app/database/` 初始化与兼容桥 |
| 数据导入、质量、修复 | `aiDoc/relations/system-map.md` | `aiDoc/data/*.md` |
| 因子、研究、回测 | `aiDoc/modules/module-index.md` | `aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/roadmap.md` |
| 外部因子池选型、策略层代码级优化 | [策略优化评审](research/20261013-2021-strategy-optimization-review.md) | `aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/hard-gates-execution-plan.md` |
| 产品定位、投资者评估、量化工具愿景 | `aiDoc/strategy/product-investment-assessment.md` | `aiDoc/strategy/quantitative-tool-vision.md` |
| 价格与价值匹配、安全边际研究及假设历史回测 | [策略定义](strategy/20260922-1314-price-value-margin-strategy.md) | `aiDoc/research/decision-factor-framework.md` |
| 选股与持仓管控路线 | `aiDoc/plans/auto-selection-position-roadmap.md` | `aiDoc/plans/hard-gates-execution-plan.md` |
| 研究产出修复与因子证据 | [研究产出最大化行动计划](plans/20261021-1708-research-output-maximization-plan.md) | `aiDoc/plans/roadmap.md`、[温层因子回填与 IC 证据](data/20261023-0247-warm-factor-ic-evidence.md) |
| 温层因子回填、IC 证据与回测口径 | [温层因子回填与 IC 证据](data/20261023-0247-warm-factor-ic-evidence.md) | `aiDoc/relations/trusted-daily-data-center.md` 第 10.1 节 |
| 估值历史回填、字段级 PIT 判定与偏差登记 | [估值历史回填](data/20261026-valuation-history-backfill.md) | `aiDoc/modules/schema-plan.md` §17、`aiDoc/data/source-ingestion-policy.md` §13 |
| 调仓频率与净收益（20 交易日调仓平台复现） | [调仓频率净收益证据](research/20261024-1205-rebalance-frequency-net-return.md) | [研究产出最大化行动计划](plans/20261021-1708-research-output-maximization-plan.md) P3 |
| 策略自演进（训练窗自动选参、rolling 选参策略） | [训练窗自动选参计划](plans/20261024-1522-training-window-selection-plan.md) | `aiDoc/plans/roadmap.md` V4.1、[首次实跑证据](research/20261024-1706-training-window-selection-first-run.md) |
| 长期事实、偏好、踩坑 | `aiDoc/memory/README.md` | 通常不需要读正式文档 |
| 某份时间戳文档现在是否还作数、谁取代了谁 | [文档状态索引](20261115-1649-doc-status-index.md) | 该文档头部的 YAML frontmatter（状态真源） |
| 自主推进小组、多角色协作 | `aiDoc/relations/20260722-1519-autonomous-agent-team.md` | `aiDoc/plans/decision-review-protocol.md`、本文件长期协作机制小节 |

## 推荐阅读顺序

1. [计划与路线 Reference](plans/README.md)
2. [系统关系 Reference](relations/system-map.md)
3. [模块索引 Reference](modules/module-index.md)
4. [V1 可信日线数据中心架构](relations/trusted-daily-data-center.md)
5. [日线数据字段契约](data/daily-bar-contract.md)
6. [数据源导入策略](data/source-ingestion-policy.md)
7. [数据质量校验与修复设计](data/quality-validation-and-repair.md)
8. [数据库 Schema 规划](modules/schema-plan.md)
9. [路线图](plans/roadmap.md)
10. [决策因子体系设计](research/decision-factor-framework.md)
11. [产品与投资者视角评估](strategy/product-investment-assessment.md)
12. [量化工具愿景](strategy/quantitative-tool-vision.md)
13. [自动选股与持仓管控路线图](plans/auto-selection-position-roadmap.md)
14. [前置硬门槛执行计划](plans/hard-gates-execution-plan.md)
15. [研究产出最大化行动计划](plans/20261021-1708-research-output-maximization-plan.md)
16. [估值历史回填与字段级 PIT 判定](data/20261026-valuation-history-backfill.md)
17. [决策与提交评审协议](plans/decision-review-protocol.md)

## 更新原则

- 根 `AGENTS.md` 是 AI 协作规则唯一真源，并按 AGENTS.md 分层嵌套约定在各模块目录放 `AGENTS.md`；`AGENT.MD` 只是兼容入口，不承载规则。
- `aiDoc/` 是项目文档唯一目录，按主题拆分，入口文件只负责导航。
- 不要把临时过程写进项目文档；只把会影响后续实现的稳定结论写进去。
- 带时间戳前缀的文档是**决策与证据的历史层**，正文一旦落定不再回改事实数字；它是否仍然作数只由头部 YAML `status` / `as_of` / `superseded_by` / `note` 表达，改动状态后重新生成[文档状态索引](20261115-1649-doc-status-index.md)。当前状态只写在 living docs（`roadmap.md`、`system-map.md`、`module-index.md`、`schema-plan.md`、`memory/README.md` 等无时间戳文件）与代码里。
