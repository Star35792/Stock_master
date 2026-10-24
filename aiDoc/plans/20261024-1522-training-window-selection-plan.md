# 训练窗自动选参（策略自演进阶段 1）计划

**制定时间**：2026-10-24 15:22
**触发原因**：用户要求「策略要能自我演进，逐渐贴近更高收益率，不要人工一点点调」；T2 四角色评审（架构师 / 产品经理 / 金融行业专家 / Vibe Coding 规则编写者）均给出**有条件通过**，无 P0 阻断项。
**变更分级**：T2（因子与策略口径、回测假设、artifact 幂等键契约）。

## 1. 定位：策略自演进子路线与既有路线的映射

策略自演进分四个切片推进，本计划只覆盖**阶段 1**：

| 自演进阶段 | 内容 | 状态 |
| --- | --- | --- |
| 1. 训练窗自动选参 | rolling 每窗在训练期内自动算 IC 证据 → 选因子/方向/权重 → 产物落 `training_artifact` → 测试窗纯样本外执行 | 本计划 |
| 2. challenger 生成器 | 在有界参数网格上循环调用阶段 1 内核生成候选配置 | 未开始 |
| 3. champion/challenger | OOS 晋级门槛 + `champion_config` 发布 | 未开始 |
| 4. 定时演进 | 调度器周期重跑 + champion 健康告警 | 未开始 |

与 `aiDoc/plans/auto-selection-position-roadmap.md` 的关系：本子路线属其**阶段 3（策略库与滚动评估）的先导切片**，不与其「不要跳阶段」门冲突——硬门槛 1-4 已按 `hard-gates-execution-plan.md` 先行补齐。它落地 roadmap「推荐近期执行顺序」第 2 条的“训练选参编排”（产物血缘、窗口绑定已由前一切片交付），即研究产出最大化计划 P3-4。

## 2. 非目标（本切片明确不做）

- champion/challenger 晋级、`|IR|` 加权（等 ≥4 窗样本后另行评审）、调度器、前端界面。
- 跨窗复合收益曲线与窗口切换成本、过拟合检验（CSCV/Bonferroni）、参数敏感性分析、候选池扩容（受 P2-4/P2-5 数据项制约）。
- 不以「净收益跑赢 ic_aligned」为验收判据（见 §5）。

## 3. `training_ic_v1` 选择政策（预注册，阈值人为设定非统计估计）

**输入**（全部仅来自训练窗）：

- 候选池：请求的 `factor_specs`（语义变为候选池；其 `weight` 不参与选择，选中者一律等权 `1`）。
- 方向先验：候选 spec 的 `higher_is_better`（脚本用 `--spec-set` 预设承载，如 `ic_aligned` = 全样本 IC 证据方向）。
- 前瞻收益：qfq `close[t+1]/close[t]-1`，t 为训练窗开市日、t+1 为 SH 日历下一开市日；目标日该 symbol 无可信 qfq 收盘则该 symbol 当日剔除；**训练窗最后一日不产生 IC 观测**（次日收益在决策时点尚不可知，不允许跨窗取未来）。
- RankIC 数学：与 `factor_cross_section` 共用同一 Decimal(prec 28) 秩相关实现（平均秩打平处理），抽取为 `app/factors/rank_ic.py` 公共模块，不新开 float 口径。

**口径差异声明**：本政策的前瞻收益取自 `market.prices_qfq`（curated 温层）并按交易所日历对齐；既有 `factor.factor_profile` 证据（`compute_warm_factor_ic.py`）用 LEAD(1) 顺延口径且为 float/DuckDB 实现。两者不可直接比数值；P1 判据（RankIC>0.01、正率>55%）总结自 LEAD 口径，本政策阈值按新口径预注册，方向先验「只维持或拒绝」的设计使口径错配不致翻转方向。同窗口双口径对照为后续项。

**逐因子判定**（三条件门槛 + 方向规则，全部预注册）：

- 统计：逐日 RankIC1 序列 → `mean`、`IR=mean/std`（总体 std）、方向对齐正率、观测数。
- 门槛：`|mean RankIC1| ≥ 0.02` 且 `|IR| ≥ 0.30` 且 `正率 ≥ 0.55` 且 `观测数 ≥ 10`。
- 方向：有效方向 = sign(mean RankIC)。与先验同号（或先验缺失）→ `maintained`；异号时仅当 `训练窗开市日数 ≥ 60` 允许 `flipped`，否则 `rejected`（40 日窗不允许凭训练窗翻转方向）。门槛不满足 → `rejected`（注明失败条件）。
- 排序截断：存活因子按 `(|IR| desc, |mean RankIC| desc, factor_name asc)` 排序，`top_k=None` 时全保留。
- fail-closed：存活因子 `< 3`（`min_surviving_factors`）→ 该窗抛错中止**整次运行**（沿用 rolling all-or-nothing 语义，错误信息携带逐因子原因表；不回退全候选等权）。候选在当窗无因子值 → 剔除该候选并记录原因，测试窗对**选中** specs 缺数据仍 fail-closed。

**config_hash8（进入 window_id 后缀）**：sha256 前 8 位 hex，输入为规范化 JSON：`{strategy_id, ic_horizon, 全部阈值, min_ic_observations, min_surviving_factors, top_k, weighting, allow_flip_min_training_days, 候选池=[factor_name@factor_version] 排序去重}`。不含请求 weight、运行结果、时间戳——同配置重跑幂等复用 artifact，异配置互不冲突。

## 4. 契约变化

- `window_id`：仅启用选择时追加 `|sel-{strategy_id}-{config_hash8}`；不启用时与现状逐字节一致。
- `training_input_snapshot`：启用时 `contract_version=rolling_training_input_v2`，新增 `selection` 块（政策参数、config_hash、逐候选统计与判定、选中 specs、qfq 价格源路径与 release tag、训练窗日历）；不启用时保持 `rolling_training_input_v1` 结构不变。
- 研究合同：`factor_specs` 恒为候选池（保验证报告可比键）；启用时新增 `selection_policy` 与 `selection_result` 键。`selection_policy` 加入 `_COMPARABLE_PARAMETER_KEYS`，混合新旧政策的 runs 聚合被拒。
- API：`POST /strategy-research/rolling-backtests` 请求新增可选 `selection_policy`（`Literal["training_ic_v1"] | None`，默认 None）；研究脚本 `run_warm_rolling_selection.py` 新增 `--selection-policy`（默认 none）。
- 无 research.duckdb / 主库 schema 变更。

## 5. 验收标准（机制成立即完成）

1. 默认不变：`selection_policy=None` 路径的 window_id、快照 v1、artifact hash 与现状逐字节一致（回归测试锁定）。**已达成。**
2. 选择可审计：每窗 artifact 可回答「为什么选这几个因子」（逐候选统计+判定+阈值+政策版本+价格源血缘，全部进 hash）。**已达成。**
3. 样本外真实成立：测试窗消费的因子集合只来自 artifact；训练窗末日不产生跨窗 IC 观测。**已达成。**
4. 确定性：同数据同代码重跑，选参结果与 artifact hash 逐窗一致。**已达成**（config hash 幂等复用有测试）。
5. 端到端证据落盘：≥4 窗（v2-qfq-2y）rolling 序列跑通并与 none 基线逐窗对照，写入 `aiDoc/research/`，结论级别标注「机制验证，非可信策略收益」，payload 含机器可读 `interpretation: "link_validation_only"`。**已达成**（seg2022 六窗完整对照 + 2024-2026 段门槛拒绝证据，见 [首次实跑证据](../research/20261024-1706-training-window-selection-first-run.md)；对照以 `--selection-min-survivors 2` 敏感性配置完成，已披露）。
6. limitations 增补：`direction_selected_from_short_training_window`、`selection_thresholds_authored_not_estimated`、`window_rejection_changes_evaluated_window_set`。**已达成。**

## 6. 评审裁决记录（2026-10-24）

四角色独立评审结论：架构师/金融专家/规则编写者「有条件通过」、产品经理「通过（附条件）」，均无 P0。P1 条件全部采纳：

| 条件 | 裁决 |
| --- | --- |
| 合同 `factor_specs` 保留候选池，选中 specs 走独立键（保验证报告可比性） | 采纳 |
| RankIC 数学抽公共模块，单一 Decimal 口径 | 采纳（`app/factors/rank_ic.py`） |
| prices_qfq 读取 fail-closed 守卫 + 血缘记录（路径/release tag 入快照） | 采纳 |
| 候选当窗缺数据剔除并记录；选中 specs 测试窗缺数据仍 fail-closed | 采纳 |
| v2 快照/后缀严格条件化；None 路径逐字节不变（测试锁定） | 采纳 |
| digest 输入规范化（排序 specs、排除 weight）、tie-break 与舍入固定 | 采纳 |
| 方向先验只维持或拒绝，40 日窗不允许翻转（≥60 日才允许）；阈值预注册、禁止按测试窗回调 | 采纳 |
| 拒绝窗口显式化：all-or-nothing 中止 + 错误携带逐因子原因表 | 采纳（两角色意见合并） |
| 首版等权，`|IR|` 加权后置 | 采纳 |
| README API 面、roadmap、研究文档、脚本 limitations 同步 | 采纳 |
| API 透传为必做（保持与脚本入口对等） | 采纳（规则编写者 P2 意见，从权） |
| 同窗口双口径（日历对齐 vs LEAD）对照量化 | 部分采纳：口径差异显式声明 + 方向先验设计消解错配风险；数值对照列为后续项 |

## 7. 文档同步清单

- 本计划（已建）；实跑后新增 `aiDoc/research/yyyyMMdd-HHmm-` 研究文档。
- `aiDoc/plans/roadmap.md`：当前状态段 + 推荐近期执行顺序第 2 条进展。
- `README.md`：核心 API 面 rolling-backtests 新字段。
- `backend/scripts/research/run_warm_rolling_selection.py` 头部限制声明按政策条件更新。
