# AI 协作与项目文档入口

本目录是 Stock Master 的统一项目文档层：既保存产品、架构、数据、schema、研究和路线设计，也保存 vibe coding 所需的轻量 reference。

## 使用规则

1. 先读根 `AGENTS.md`，再读编辑目标所在目录链上的模块 `AGENTS.md`（模块清单以根 `AGENTS.md` 的「作用域与读取顺序」为准，不在本文件复制），最后读本文件。
2. 根据任务类型只读下方相关 reference。
3. 只有任务触碰对应边界时，才继续打开深入设计文档。
4. 引用任何带时间戳前缀的文档前，先看它头部的 YAML `status`；批量判断口径新旧读[文档状态索引](20261115-1649-doc-status-index.md)。无时间戳前缀的 living docs 才承载当前状态。
5. 文档同步范围以根 `AGENTS.md` 的「文档同步门槛」为准。

## 协作规则入口

角色与触发条件见根 [`AGENTS.md`](../AGENTS.md)；T0/T1/T2 分级和 T2 评审流程见[决策与提交评审协议](plans/decision-review-protocol.md)。本文件只负责文档导航，不另设协作顺序。

## Reference Map

| 任务类型 | 先读 reference | 必要时再读深入文档 |
| --- | --- | --- |
| 重大决策、AI 提交前评审 | `aiDoc/plans/decision-review-protocol.md` | 按受影响边界读取下方对应文档 |
| 阶段计划、路线、MVP | `aiDoc/plans/README.md` | `aiDoc/plans/roadmap.md` |
| 架构、数据流、存储边界 | `aiDoc/relations/system-map.md` | `aiDoc/relations/trusted-daily-data-center.md` |
| 本机单份数据保留、冗余清理与容量验收 | [本机存储保留与清理交接](relations/20261112-1341-local-storage-retention-and-cleanup.md) | [历史存储诊断](relations/20261013-2207-storage-diagnosis-and-plan.md)、`aiDoc/relations/system-map.md` |
| 真实持仓账本存储（SQLite 例外）、持仓分析与偏离检测 | [持仓账本 SQLite ADR](relations/20261023-1925-position-ledger-sqlite.md) | `aiDoc/plans/roadmap.md` V5.4、`aiDoc/modules/schema-plan.md` |
| 组合偏离覆盖草案外持仓、逐仓报价日期 | [偏离覆盖决策](research/20261117-0116-portfolio-drift-coverage.md) | [持仓账本 SQLite ADR](relations/20261023-1925-position-ledger-sqlite.md)、`aiDoc/plans/roadmap.md` V5.4 |
| 组合草案发现与持仓页选择 | [草案发现决策](research/20261117-0129-portfolio-plan-discovery.md) | [偏离覆盖决策](research/20261117-0116-portfolio-drift-coverage.md)、`aiDoc/plans/roadmap.md` V5.4 |
| 旧主库组合草案列兼容 | [组合草案旧主库列兼容](research/20261117-0751-portfolio-plan-schema-compatibility.md) | `aiDoc/modules/schema-plan.md`、`backend/app/database/session.py` |
| 组合风险预算、因子覆盖和持仓预填 | [组合风险预算与持仓预填](research/20261117-0933-portfolio-risk-budget-workflow.md) | `aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/roadmap.md` V5.4 |
| 研究库拆分（research.duckdb）、DuckDB 跨进程并发 | [研究库拆分 ADR](relations/20261024-1427-research-database-split.md) | `aiDoc/relations/system-map.md`、`aiDoc/modules/schema-plan.md` |
| 主库打不开、WAL 回放失败、DuckDB 存储可用性 | [不可回放 WAL 的无损恢复与预防](relations/20261106-1442-duckdb-unreplayable-wal-recovery.md) | `aiDoc/memory/README.md`、`aiDoc/modules/module-index.md` |
| 控制面空闲空间回收、索引与序列恢复 | [控制面主库空闲空间回收与索引/序列恢复](relations/20261109-1648-control-plane-compaction-and-index-recovery.md) | `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`、`aiDoc/memory/README.md` |
| 外部数据源接入评估、跨源对账、多源一致性 | [a-stock-data 接入评估](relations/20261111-1415-a-stock-data-integration-assessment.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/data/quality-validation-and-repair.md` |
| 多源对账激活、数据源注册表、独立第二源接入 | [多源对账激活与独立第二源接入](relations/20261111-1458-multi-source-reconciliation-activation.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/relations/trusted-daily-data-center.md` |
| 估值第二独立源、TTM PE 补齐、跨源估值对账口径 | [腾讯行情接入：估值的第二独立源与 TTM PE 补齐](relations/20261111-1531-tencent-valuation-source.md) | `aiDoc/data/source-ingestion-policy.md`、`aiDoc/data/quality-validation-and-repair.md` |
| 后端分层、代码规则 | `aiDoc/modules/backend-layer-rules.md` | 相关 `backend/app/**` 文件 |
| 前后端能力覆盖、页面与 API 差距 | [前后端能力覆盖与差距分析](plans/20261118-1435-frontend-backend-capability-gap-analysis.md) | `frontend/src/router.ts`、`frontend/src/pages/`、`backend/app/api/router.py` |
| Hikyuu / NTQ / TickFlow 的代码、研究方法与交互吸收 | [三个本地参考项目吸收方案](plans/20261118-1711-external-project-absorption-plan.md) | `aiDoc/plans/roadmap.md`；方案内候选台账、切片准入与来源摘要 |
| 信号前缀行为审计、原运行核验与 A2 事件证据合同 | [信号行为审计首片](research/20261118-1746-signal-behavior-audit.md) | `aiDoc/plans/roadmap.md`；因子到信号的范围、状态与资源上限 |
| 现金分红合成账本、既有运行单/双轴敏感性比较 | [分红情景与敏感性首片](research/20261118-2017-dividend-scenario-and-sensitivity.md) | `aiDoc/plans/roadmap.md`；合成税费/应收口径、固定合同与输入身份、缺失窗口及导出边界 |
| 训练窗 RankIC 统计诊断、HAC/BH 与旧产物边界 | [训练窗统计诊断首片](research/20261118-2036-training-ic-statistical-diagnostics.md) | [训练窗自动选参计划](plans/20261024-1522-training-window-selection-plan.md)；逐日证据、固定日期网格、声明检验族、容量与不晋级边界 |
| schema、表、迁移 | `aiDoc/modules/module-index.md` | `aiDoc/modules/schema-plan.md`、`backend/app/database/` 初始化与兼容桥 |
| 数据导入、质量、修复 | `aiDoc/relations/system-map.md` | `aiDoc/data/*.md` |
| 因子、研究、回测 | `aiDoc/modules/module-index.md` | `aiDoc/research/decision-factor-framework.md`、`aiDoc/plans/roadmap.md` |
| 回测列表与详情夏普、研究比较预览 | [历史回测夏普与比较预览决策](research/20261116-1508-backtest-research-comparison-preview.md) | [早期展示决策](research/20261116-1440-backtest-sharpe-visibility.md)、`aiDoc/plans/roadmap.md` V4 |
| 回测候选筛选、比较前口径检查 | [回测候选筛选决策](research/20261116-1546-backtest-run-discovery.md) | `aiDoc/plans/roadmap.md` V4 |
| 回测基准差值、策略合同差异 | [策略合同差异决策](research/20261116-1619-backtest-strategy-contract-diff.md) | [基准差值决策](research/20261116-1606-backtest-comparison-baseline-deltas.md)、`aiDoc/plans/roadmap.md` V4 |
| 回测选择与基准的链接恢复 | [比较链接决策](research/20261116-1716-backtest-comparison-links.md) | [策略合同差异决策](research/20261116-1619-backtest-strategy-contract-diff.md)、`aiDoc/plans/roadmap.md` V4 |
| 回测比较的净值与回撤走势 | [同日曲线决策](research/20261116-1734-backtest-comparison-curves.md) | [基准差值决策](research/20261116-1606-backtest-comparison-baseline-deltas.md)、`aiDoc/plans/roadmap.md` V4 |
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
| 自主推进小组、多角色协作 | `aiDoc/relations/20260722-1519-autonomous-agent-team.md` | 根 `AGENTS.md` 的「长期协作团队」、`aiDoc/plans/decision-review-protocol.md` |

## 更新原则

- `aiDoc/` 按主题保存项目文档；临时过程留在任务或审查记录，稳定结论写入对应主题文档。
- 时间戳文档保存决策与证据历史，状态真源在其 YAML frontmatter；当前状态写入无时间戳的 living docs。状态字段变化后运行 `scripts/doc_status_index.py` 重建[文档状态索引](20261115-1649-doc-status-index.md)。详细格式与例外见根 `AGENTS.md` 的「文档维护」。
