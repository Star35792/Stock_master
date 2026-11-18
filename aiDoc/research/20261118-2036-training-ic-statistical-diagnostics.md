---
doc_type: decision
status: current
as_of: 2026-11-18
note: >-
  三项目吸收方案 C 的统计可信度首片：只为新 training_ic_v1 训练产物
  当场记录逐日 RankIC 证据并计算固定 lag=1 HAC 与声明候选族 BH；
  不回填旧产物，不改变选择、资格或执行。
---

# 训练窗 RankIC 统计诊断首片

## 交付与边界

承接[外部项目吸收方案](../plans/20261118-1711-external-project-absorption-plan.md)与[分红情景及敏感性首片](20261118-2017-dividend-scenario-and-sensitivity.md)。本片交付 C 的第一个可核验闭环：滚动回测仍以 `selection_policy=training_ic_v1` 执行原有训练窗选择；调用方另显式设置 `training_diagnostics_policy=training_ic_hac_bh_v1` 时，选择器在训练当场记录逐日 RankIC 证据，写入新的不可变训练产物，并在回测详情按需只读展示。

诊断固定 `research_only=true`、`selection_unchanged=true`、`qualification_unchanged=true`。它不读取测试窗收益，不修改选择闸门，不把因子晋级为 `core`，不改变 `training_artifact_verified=false`，也不连接组合、paper 或人工持仓。p/q 值是所记录训练窗的统计诊断，不是未来盈利概率。

旧训练产物没有逐日 RankIC、共同证券数和缺失日期证据，不能从聚合的 mean RankIC/ICIR 反推 HAC。`GET /api/v1/backtests/runs/{run_id}/training-diagnostics` 对旧产物返回 `not_recorded / insufficient_artifact_evidence`，禁止从当前温层因子或价格回填历史。

## 证据合同与容量

新产物的窗口身份在原 `|sel-training_ic_v1-<hash8>` 后追加 `|diag-training_ic_hac_bh_v1-<hash8>`，避免改变或碰撞既有唯一 `window_id`。默认未设置诊断政策时，原选择摘要与 artifact 哈希路径保持不变。

每个声明候选按完整训练交易日网格（最后一日没有窗内 forward close，故不属于 eligible 信号日）记录：

- 日期、原始双侧 RankIC、共同证券数；
- `recorded`、`no_factor_values`、`missing_forward_prices`、`insufficient_pairs`、`zero_dispersion`；
- 全部 eligible 日期、逐日证据 SHA-256；
- 实际用于 forward return 的 QFQ 价格行数、内容 SHA-256 与 release/path 来源；
- 声明候选数、可检验数、不可检验数、诊断政策及配置哈希。

诊断只允许最多 32 个候选、252 个训练开市日，序列化诊断上限 2,000,000 字节；详情仓储先投影运行、产物 ID 与 JSON 字符数，超过 4,000,000 字符不物化。逐日证据只存一次于 `training_artifact.training_input_snapshot.selection.diagnostics`；每个 backtest run 参数只保留 artifact ID/hash、合同、状态和证据摘要引用，避免复制完整序列。没有新增表、缓存或第二事实源。

## HAC 与 BH 口径

对每个候选的有效 RankIC 值 `x_t`，以有效观测均值形成残差 `e_t=x_t-mean(x)`。首片预注册 `lag=1`，Bartlett 权重 `w_1=1/2`：

`S = Σ e_t² + 2*w_1*Σ e_t*e_(t-1)`，`SE_HAC(mean) = sqrt(S) / n`。

第二项只使用完整交易日网格上真正相邻且两端都有效的观测；缺日不会把之后的值压缩成相邻日。`n<10`、零/非正 HAC 方差或无法形成 RankIC 时返回不可计算，不补零。双侧零假设为 mean RankIC=0，`z=mean/SE`，p 值使用渐近标准正态双侧近似；方向维持/翻转只展示，不进入单侧检验。

BH 家族固定为同一窗口、同一次预注册选择的所有声明 `factor_name@factor_version`。可检验 p 值按升序计算；分母使用声明候选总数，因此不可检验候选不会被静默排除为更小的家族，也不会被赋 p=0。`alpha=0.05` 仅标记该训练窗是否拒绝零假设；前端不按 p/q 排名。跨窗口、历史尝试、反复查看和调参后的新尝试不属于同一首片家族，因此本能力不声称控制这些选择产生的总体 FDR。

## 验证、评审与后续

本片按 T2：改变训练统计证据合同并新增公共 API/详情工作流。金融独立复核与架构/产品独立复核均给出“有条件通过”，共同阻断了从旧聚合产物反推 p 值的初始方案；采纳逐日证据、完整日期网格、双侧 HAC、声明家族、独立 artifact 身份、容量上限及旧产物不回填后，当前无已知 P0/P1。

专项测试覆盖 HAC 固定网格、缺日不压缩、零方差、样本不足、BH 单调/顺序不变/声明家族分母、诊断开关与预算、旧/超大产物、404 映射、默认选择结果不变，以及前端手动加载、单飞、切换运行取消迟到响应、四类可用状态与明确概率声明。正式全量门与真实前后端页面验收结果以本轮任务最终记录为准。

仍未完成：训练内相关去重、purge/embargo 与嵌套验证、完整尝试历史、跨尝试 FDR、独立最终留出集消费记录，以及 D 的 baseline/challenger 持续复验。上述能力需要新的专项合同，不能由本片的 `reject_null` 推断。

文档影响：同步根 README API、Reference Map、计划导航与 roadmap；无 schema、事实源或架构边界变化，不改 `schema-plan`、`module-index`、`system-map` 或可信数据中心合同。
