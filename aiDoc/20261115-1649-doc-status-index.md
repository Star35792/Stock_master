---
doc_type: index
status: current
as_of: 2026-11-15
note: >-
  本表由各时间戳文档自身的 YAML frontmatter 生成（`uv run --project backend python scripts/doc_status_index.py`），状态真源是 frontmatter；索引不一致由 backend/tests/test_doc_status_index.py 挡住。
---
# 文档状态索引

`aiDoc/` 下所有带时间戳前缀的文档都有一段 YAML frontmatter，标注它现在还能不能当现状用。本索引按状态聚合，供人和 AI 在引用历史文档前先查一次。

## 状态词表

- `current`（现行有效）：决策或口径仍然成立，可以作为当前依据引用。正文中的运行数字是当时快照，不因数字变化而降状态。
- `snapshot`（一次性快照）：某次实验/回测/清单的结果记录，本身不声称仍然成立；引用时必须连同其日期与当时的偏差声明一起带出。
- `partially_superseded`（部分被取代）：决策骨架仍在位，但正文里的某些事实已被更新的文档改写。只能引用未被取代的部分，取代去向见 `superseded_by`。
- `withdrawn`（已撤销）：建议已被整体撤回，不得作为当前口径引用，仅保留作审计。

字段约定：`doc_type` 取 `decision` / `plan` / `evidence` / `analysis` / `config` / `index`；`as_of` 是该文档口径的最后核实日期，不是文件名里的生成时刻；`superseded_by` 列出取代它的文档，**路径相对 `aiDoc/`**（`../` 开头表示仓库根方向）；`note` 说明「哪一部分仍然作数」。

## 双重标注

一份文档可能同时「被当作现行依据引用」且「正文部分事实已过期」，这由 `status` 与 `note` 分别表达，不必为此新增第三种状态。例如 [研究库拆分决策](relations/20261024-1427-research-database-split.md) 的 `status: current` 表示拆分决策仍在执行，其 `note` 说明研究库表数已由 6 增为 7。

## 引用规则

- living docs（不匹配时间戳命名的 `roadmap.md`、`system-map.md`、`module-index.md`、`schema-plan.md`、`README.md` 等）承载当前状态，不带 frontmatter；时间戳文档只承载当时的决策与证据。
- 两者冲突时以 living docs 与代码为准，并按 `aiDoc/README.md` 的同步规则回写时间戳文档的 `note` 或补一份新 ADR。
- 本文件不复制各文档的结论，只做状态聚合；要读结论请打开原文。

## 现行有效 `status: current`（29 份）

| 文档 | 类型 | 截至 | 取代/去向 | 备注 |
| --- | --- | --- | --- | --- |
| [`plans/20261024-1522-training-window-selection-plan.md`](plans/20261024-1522-training-window-selection-plan.md) | plan | 2026-10-24 | — | — |
| [`plans/20261119-1014-operations-recovery-and-index-consistency.md`](plans/20261119-1014-operations-recovery-and-index-consistency.md) | decision | 2026-11-19 | — | — |
| [`plans/20261119-1049-agent-overall-plan-and-sqlite.md`](plans/20261119-1049-agent-overall-plan-and-sqlite.md) | plan | 2026-11-19 | — | 汇总本轮用户完整目标与分阶段验收。第一阶段为 SQLite 会话与显式记忆； 结构化计划、统一权限、自动执行与失败恢复尚未交付，写执行默认关闭。 |
| [`relations/20261024-1427-research-database-split.md`](relations/20261024-1427-research-database-split.md) | decision | 2026-10-24 | — | 决策不变；文中「6 张表」现为 7 张，研究库另含 strategy.external_observation。 |
| [`relations/20261111-0300-intraday-snapshot-guard.md`](relations/20261111-0300-intraday-snapshot-guard.md) | decision | 2026-11-11 | — | — |
| [`relations/20261111-1458-multi-source-reconciliation-activation.md`](relations/20261111-1458-multi-source-reconciliation-activation.md) | decision | 2026-11-11 | — | — |
| [`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | decision | 2026-11-11 | — | — |
| [`relations/20261111-1855-alternative-data-scheduling-and-hot-money-board.md`](relations/20261111-1855-alternative-data-scheduling-and-hot-money-board.md) | decision | 2026-11-11 | — | — |
| [`relations/20261111-1930-six-family-pit-audit.md`](relations/20261111-1930-six-family-pit-audit.md) | evidence | 2026-11-11 | — | 六族均为另类数据/情绪族因子，与行业时点无关；审计台账仍是当前依据，文中测试基线数字（1,430 passed）是当日快照。 |
| [`relations/20261111-2050-alternative-history-backfill.md`](relations/20261111-2050-alternative-history-backfill.md) | decision | 2026-11-11 | — | — |
| [`relations/20261112-1005-tencent-quote-day-roll.md`](relations/20261112-1005-tencent-quote-day-roll.md) | decision | 2026-11-12 | — | — |
| [`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | decision | 2026-11-12 | — | 保留边界、清理清单与验收口径仍是当前依据；文中「项目级 AI 规则以 AGENT.MD 为准」的指针已过时，规则真源现为仓库根 AGENTS.md（AGENT.MD 只作兼容入口）。 文中因执行环境拒绝而留下的空目录 `backups/wal-fix-20261026/` 已于 2026-11-15 成功移除；`backups/` 下 16,852,538 字节的三份历史修复输入仍在位，仍按本文口径保留。 |
| [`research/20261020-2222-deducted-earnings-factor-optimization.md`](research/20261020-2222-deducted-earnings-factor-optimization.md) | decision | 2026-10-20 | — | — |
| [`research/20261024-1205-rebalance-frequency-net-return.md`](research/20261024-1205-rebalance-frequency-net-return.md) | evidence | 2026-10-24 | — | 调仓频率与净收益口径仍是当前结论；文中 P2 偏差源之「行业时点缺失」已于 2026-11-11 关闭，因子资格输入见 20261027-2122。 |
| [`research/20261024-1800-quantile-and-rolling-curve.md`](research/20261024-1800-quantile-and-rolling-curve.md) | decision | 2026-10-24 | — | — |
| [`research/20261027-2122-strategy-readiness-audit.md`](research/20261027-2122-strategy-readiness-audit.md) | evidence | 2026-10-27 | — | 资格审计仍是当前前置结论；其中行业时点一条已被 2026-11-11 申万区间交付取代。 |
| [`research/20261028-0157-quality-value-attention-strategy.md`](research/20261028-0157-quality-value-attention-strategy.md) | decision | 2026-10-28 | — | — |
| [`research/20261102-2152-factor-horizon-observation-gate.md`](research/20261102-2152-factor-horizon-observation-gate.md) | decision | 2026-11-02 | — | — |
| [`research/20261103-0313-factor-profile-evidence-identity.md`](research/20261103-0313-factor-profile-evidence-identity.md) | decision | 2026-11-03 | — | — |
| [`research/20261116-1508-backtest-research-comparison-preview.md`](research/20261116-1508-backtest-research-comparison-preview.md) | decision | 2026-11-16 | — | — |
| [`research/20261116-1606-backtest-comparison-baseline-deltas.md`](research/20261116-1606-backtest-comparison-baseline-deltas.md) | decision | 2026-11-16 | — | — |
| [`research/20261116-1619-backtest-strategy-contract-diff.md`](research/20261116-1619-backtest-strategy-contract-diff.md) | decision | 2026-11-16 | — | — |
| [`research/20261116-1716-backtest-comparison-links.md`](research/20261116-1716-backtest-comparison-links.md) | decision | 2026-11-16 | — | — |
| [`research/20261117-0116-portfolio-drift-coverage.md`](research/20261117-0116-portfolio-drift-coverage.md) | decision | 2026-11-17 | — | — |
| [`research/20261117-0751-portfolio-plan-schema-compatibility.md`](research/20261117-0751-portfolio-plan-schema-compatibility.md) | decision | 2026-11-17 | — | — |
| [`research/20261117-0933-portfolio-risk-budget-workflow.md`](research/20261117-0933-portfolio-risk-budget-workflow.md) | decision | 2026-11-17 | — | — |
| [`research/20261118-2017-dividend-scenario-and-sensitivity.md`](research/20261118-2017-dividend-scenario-and-sensitivity.md) | decision | 2026-11-18 | — | 三项目吸收方案 A2 的合成现金分红账本及 B 的既有运行敏感性比较首片。 不接入真实公司行为，不改写旧运行，不授予 PIT、收益或执行资格。 |
| [`research/20261118-2036-training-ic-statistical-diagnostics.md`](research/20261118-2036-training-ic-statistical-diagnostics.md) | decision | 2026-11-18 | — | 三项目吸收方案 C 的统计可信度首片：只为新 training_ic_v1 训练产物 当场记录逐日 RankIC 证据并计算固定 lag=1 HAC 与声明候选族 BH； 不回填旧产物，不改变选择、资格或执行。 |
| [`strategy/20260922-1314-price-value-margin-strategy.md`](strategy/20260922-1314-price-value-margin-strategy.md) | decision | 2026-11-16 | — | 活合同文档，正文按最新版本维护（@v1/@v2 已 retired，@v3 生效），as_of 取最近一次口径核实日。 |

## 一次性快照 `status: snapshot`（9 份）

| 文档 | 类型 | 截至 | 取代/去向 | 备注 |
| --- | --- | --- | --- | --- |
| [`data/20261016-0024-low-price-oversold-screen.md`](data/20261016-0024-low-price-oversold-screen.md) | evidence | 2026-10-16 | — | — |
| [`data/20261019-low-price-rebound-backtest.md`](data/20261019-low-price-rebound-backtest.md) | evidence | 2026-10-19 | — | — |
| [`plans/20261118-1435-frontend-backend-capability-gap-analysis.md`](plans/20261118-1435-frontend-backend-capability-gap-analysis.md) | analysis | 2026-11-18 | — | — |
| [`research/20261024-1706-training-window-selection-first-run.md`](research/20261024-1706-training-window-selection-first-run.md) | evidence | 2026-10-24 | — | — |
| [`research/20261024-1822-pool-expansion-price-volume-ext.md`](research/20261024-1822-pool-expansion-price-volume-ext.md) | evidence | 2026-10-24 | — | — |
| [`research/20261027-2003-single-factor-stability-review.md`](research/20261027-2003-single-factor-stability-review.md) | evidence | 2026-10-27 | — | 文中「历史行业时点不足」的偏差已被 2026-11-11 交付关闭，其余偏差声明仍是本次快照的一部分。 |
| [`research/20261027-2027-exposure-budget-comparison.md`](research/20261027-2027-exposure-budget-comparison.md) | evidence | 2026-10-27 | — | — |
| [`research/20261116-2207-backtest-comparison-acceptance.md`](research/20261116-2207-backtest-comparison-acceptance.md) | evidence | 2026-11-16 | — | — |
| [`research/20261117-0310-portfolio-plan-discovery-acceptance.md`](research/20261117-0310-portfolio-plan-discovery-acceptance.md) | evidence | 2026-11-17 | — | — |

## 部分被取代 `status: partially_superseded`（25 份）

| 文档 | 类型 | 截至 | 取代/去向 | 备注 |
| --- | --- | --- | --- | --- |
| [`data/20261015-0254-historical-attribute-gaps.md`](data/20261015-0254-historical-attribute-gaps.md) | analysis | 2026-10-15 | [`data/20261016-0024-low-price-oversold-screen.md`](data/20261016-0024-low-price-oversold-screen.md)、[`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | 本文所列「行业时点」缺口已于 2026-11-11 由申万生效区间交付关闭；ST 与退市时点缺口继续挂账。 |
| [`data/20261023-0247-warm-factor-ic-evidence.md`](data/20261023-0247-warm-factor-ic-evidence.md) | evidence | 2026-10-23 | [`research/20261027-2122-strategy-readiness-audit.md`](research/20261027-2122-strategy-readiness-audit.md) | IC 数字是 v2-qfq-2y 旧口径下的日级快照，因子池资格以 2026-10-27 审计为准。 |
| [`data/20261026-valuation-history-backfill.md`](data/20261026-valuation-history-backfill.md) | evidence | 2026-10-26 | [`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | §7「行业时点不可得」只否证东财 BOARD_NAME 这一条路径，已被 2026-11-11 申万区间交付取代；估值字段级 PIT 判定与偏差登记继续有效。 |
| [`data/20261027-0118-daily-data-factor-closure.md`](data/20261027-0118-daily-data-factor-closure.md) | evidence | 2026-10-27 | [`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | 数据→因子闭环链路继续有效；其中行业族按当前标签口径的部分已被时点区间交付取代。 |
| [`plans/20261021-1708-research-output-maximization-plan.md`](plans/20261021-1708-research-output-maximization-plan.md) | plan | 2026-10-21 | [`plans/20261025-2340-data-backfill-review-brief.md`](plans/20261025-2340-data-backfill-review-brief.md) | 优先级与工作量估算为计划快照，执行事实以补齐复核简报与其后的 ADR 为准。 |
| [`plans/20261025-2340-data-backfill-review-brief.md`](plans/20261025-2340-data-backfill-review-brief.md) | plan | 2026-10-25 | [`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | 方案 C 的行业时点部分已于 2026-11-11 落地；ST 历史与指数成分历史序列仍为待决选项。 |
| [`plans/20261118-1711-external-project-absorption-plan.md`](plans/20261118-1711-external-project-absorption-plan.md) | plan | 2026-11-18 | — | Hikyuu、New Tea Quant、TickFlow 两轮本地源码扫描的吸收方案与证据快照。A1、A2、B 首片及 C 训练窗统计诊断首片已交付；当前状态见取代文档及其引用。其余候选、准入条件和来源证据继续有效，不改变事实源或策略资格。真实公司行为仍 reject，候选内部顺序不替代 roadmap 的正式近期优先级。 |
| [`relations/20260722-1519-autonomous-agent-team.md`](relations/20260722-1519-autonomous-agent-team.md) | config | 2026-07-22 | [`../AGENTS.md`](../AGENTS.md) | 文中引用的规则真源 AGENT.MD 已降级为兼容入口，协作规则与长期小组口径以仓库根 AGENTS.md 为准；本文保留为历史小组配置。 |
| [`relations/20260722-1721-tiered-storage-architecture.md`](relations/20260722-1721-tiered-storage-architecture.md) | decision | 2026-07-22 | [`relations/20261023-1925-position-ledger-sqlite.md`](relations/20261023-1925-position-ledger-sqlite.md)、[`relations/20261024-1427-research-database-split.md`](relations/20261024-1427-research-database-split.md)、[`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md)、[`plans/20261119-1049-agent-overall-plan-and-sqlite.md`](plans/20261119-1049-agent-overall-plan-and-sqlite.md) | 分层思路仍在位；具体库划分与清理策略由后续决策扩展或取代，新增独立智能体 SQLite 状态库。 |
| [`relations/20261021-1045-storage-split-phase1.md`](relations/20261021-1045-storage-split-phase1.md) | decision | 2026-10-21 | [`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | 决策在位，文中的容量与行数数字是当日执行快照。 |
| [`relations/20261021-1150-storage-split-phase2-warm-layer.md`](relations/20261021-1150-storage-split-phase2-warm-layer.md) | decision | 2026-10-21 | [`relations/20261109-1648-control-plane-compaction-and-index-recovery.md`](relations/20261109-1648-control-plane-compaction-and-index-recovery.md)、[`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | 决策在位，文中的容量与行数数字是当日执行快照。 |
| [`relations/20261021-1424-storage-split-phase3-warm-layer-extension.md`](relations/20261021-1424-storage-split-phase3-warm-layer-extension.md) | decision | 2026-10-21 | [`relations/20261109-1648-control-plane-compaction-and-index-recovery.md`](relations/20261109-1648-control-plane-compaction-and-index-recovery.md) | 决策在位，文中的容量与行数数字是当日执行快照。 |
| [`relations/20261023-1925-position-ledger-sqlite.md`](relations/20261023-1925-position-ledger-sqlite.md) | decision | 2026-10-23 | [`relations/20261024-1427-research-database-split.md`](relations/20261024-1427-research-database-split.md)、[`plans/20261119-1049-agent-overall-plan-and-sqlite.md`](plans/20261119-1049-agent-overall-plan-and-sqlite.md) | 持仓账本使用独立 SQLite 的决定继续有效；其余域均在单 DuckDB、SQLite 为唯一例外的全局表述， 已由研究分库和后续智能体 SQLite 状态库决定扩展。 |
| [`relations/20261106-1442-duckdb-unreplayable-wal-recovery.md`](relations/20261106-1442-duckdb-unreplayable-wal-recovery.md) | decision | 2026-11-06 | [`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | ATTACH 无损恢复手法仍在用；WAL 处置与备份口径以本地存储保留与清理决策为准。 |
| [`relations/20261109-1648-control-plane-compaction-and-index-recovery.md`](relations/20261109-1648-control-plane-compaction-and-index-recovery.md) | decision | 2026-11-09 | [`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | CTAS 压缩与序列/索引恢复流程仍在位；文中测试基线与空间数字是当日快照。 |
| [`relations/20261111-1415-a-stock-data-integration-assessment.md`](relations/20261111-1415-a-stock-data-integration-assessment.md) | analysis | 2026-11-11 | [`relations/20261111-1458-multi-source-reconciliation-activation.md`](relations/20261111-1458-multi-source-reconciliation-activation.md)、[`relations/20261111-1531-tencent-valuation-source.md`](relations/20261111-1531-tencent-valuation-source.md)、[`relations/20261111-1810-industry-point-in-time-history.md`](relations/20261111-1810-industry-point-in-time-history.md) | 评估结论中的 P0/P1 已由同日两份决策落地，P3 行业时点历史已由 20261111-1810 交付；其余分期建议仍未执行。 |
| [`relations/20261111-1531-tencent-valuation-source.md`](relations/20261111-1531-tencent-valuation-source.md) | decision | 2026-11-11 | [`relations/20261112-1005-tencent-quote-day-roll.md`](relations/20261112-1005-tencent-quote-day-roll.md) | 接入决策在位；取哪一天收盘的判定被行情日滚动决策修正。 |
| [`research/20260722-1511-quant-readiness-gap-analysis.md`](research/20260722-1511-quant-readiness-gap-analysis.md) | analysis | 2026-07-22 | — | 缺口清单大量条目已在后续 ADR 中关闭，引用前先核对当前 roadmap 现状章节。 |
| [`research/20261013-2021-strategy-optimization-review.md`](research/20261013-2021-strategy-optimization-review.md) | analysis | 2026-10-13 | — | 评审建议为当时切片，落地情况以 roadmap 与各 ADR 为准。 |
| [`research/20261014-1445-project-optimization-proposal-1.md`](research/20261014-1445-project-optimization-proposal-1.md) | analysis | 2026-10-14 | — | 提案为当时切片，其中存储与备份建议已被撤销或改写。 |
| [`research/20261116-1440-backtest-sharpe-visibility.md`](research/20261116-1440-backtest-sharpe-visibility.md) | decision | 2026-11-16 | — | — |
| [`research/20261116-1546-backtest-run-discovery.md`](research/20261116-1546-backtest-run-discovery.md) | decision | 2026-11-16 | — | — |
| [`research/20261116-1734-backtest-comparison-curves.md`](research/20261116-1734-backtest-comparison-curves.md) | decision | 2026-11-16 | — | — |
| [`research/20261117-0129-portfolio-plan-discovery.md`](research/20261117-0129-portfolio-plan-discovery.md) | decision | 2026-11-17 | — | — |
| [`research/20261118-1746-signal-behavior-audit.md`](research/20261118-1746-signal-behavior-audit.md) | decision | 2026-11-18 | — | 三项目吸收方案 A1 的首个前后端实现：有界内存行为审计器及因子选股运行适配器。 只核验 factor_frame 到 signal 的抽样稳定性，不认证历史 PIT、收益或公司行为会计。 A1 已交付事实与范围继续有效。A2 仅有合同、B–H 全部待实施的状态被后续文档部分取代： 已新增 A2 合成工具和 B 比较首片，真实事件引擎与其余能力仍待准入。 |

## 已撤销 `status: withdrawn`（1 份）

| 文档 | 类型 | 截至 | 取代/去向 | 备注 |
| --- | --- | --- | --- | --- |
| [`relations/20261013-2207-storage-diagnosis-and-plan.md`](relations/20261013-2207-storage-diagnosis-and-plan.md) | plan | 2026-10-13 | [`relations/20261112-1341-local-storage-retention-and-cleanup.md`](relations/20261112-1341-local-storage-retention-and-cleanup.md) | 保留灾备副本、滚动备份、直接清理 WAL 与保留 30 份筛选快照的建议均已被撤销，不得作为当前口径引用。 |

合计 64 份带时间戳文档。
