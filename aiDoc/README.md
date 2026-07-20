# AI 协作与项目文档入口

本目录是 Stock Master 的统一项目文档层：既保存产品、架构、数据、schema、研究和路线设计，也保存 vibe coding 所需的轻量 reference。

## 使用规则

1. 先读 `AGENT.MD` 和本文件。
2. 根据任务类型只读下方相关 reference。
3. 只有任务触碰对应边界时，才继续打开深入设计文档。
4. 完成代码任务前做一次文档影响检查：能力、API、schema、架构、路线、数据规则或长期记忆发生变化，就同步对应文档。

## Reference Map

| 任务类型 | 先读 reference | 必要时再读深入文档 |
| --- | --- | --- |
| 重大决策、AI 提交前评审 | `aiDoc/plans/decision-review-protocol.md` | 按受影响边界读取下方对应文档 |
| 阶段计划、路线、MVP | `aiDoc/plans/README.md` | `aiDoc/plans/roadmap.md` |
| 架构、数据流、存储边界 | `aiDoc/relations/system-map.md` | `aiDoc/relations/trusted-daily-data-center.md` |
| 后端分层、代码规则 | `aiDoc/modules/backend-layer-rules.md` | 相关 `backend/app/**` 文件 |
| schema、表、迁移 | `aiDoc/modules/module-index.md` | `aiDoc/modules/schema-plan.md`、相关 Alembic |
| 数据导入、质量、修复 | `aiDoc/relations/system-map.md` | `aiDoc/data/*.md` |
| 因子、研究、回测 | `aiDoc/modules/module-index.md` | `aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/roadmap.md` |
| 长期事实、偏好、踩坑 | `aiDoc/memory/README.md` | 通常不需要读正式文档 |

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
11. [决策与提交评审协议](plans/decision-review-protocol.md)

## 更新原则

- `AGENT.MD` 是 AI 协作规则唯一真源。
- `aiDoc/` 是项目文档唯一目录，按主题拆分，入口文件只负责导航。
- 不要把临时过程写进项目文档；只把会影响后续实现的稳定结论写进去。
