---
doc_type: evidence
status: snapshot
as_of: 2026-10-24
---
# 价量候选池扩容首跑：默认门槛重新可用 + seg2022 转正 + 2024-2026 段仍 fail-closed

- 日期：2026-10-24
- 结论级别：**机制验证 + 增量证据（不是可信策略收益）**
- 前序：[训练窗自动选参首次实跑](20261024-1706-training-window-selection-first-run.md)（下一步第 1 条「扩候选池」的价量部分）
- 运行：`strategy.backtest_run` #57-62（seg2022 扩池 6 窗全 success）；2024-2026 段窗口 0 fail-closed 拒绝（预期行为）
- 脚本：`backend/scripts/factors/backfill_warm_factors.py`（新增 3 因子）+ `run_warm_rolling_selection.py --spec-set price_volume_wide_ext`

## 一句话结论

把温层价量候选池从 13 个扩到 16 个（新增 `abnormal_volume_pressure` / `beta_60` / `residual_volatility_60`）后：**seg2022 困难段的 `training_ic_v1` 默认门槛（存活≥3）首次全程可用**，6 窗均值从 -0.13%（13 因子池，需事后放宽到 2）转为 **+2.43%/窗**，最差回撤从 -25.75% 收窄到 **-17.95%**；而 2024-2026 段窗口 0 仍被 fail-closed 拒绝（16 候选仅 2 存活）——**价量池扩容到此为止，该段要过门槛必须引入财务/估值因子（P2-5）**。

## 扩池内容

- `abnormal_volume_pressure`：volume / mean(前 20 日 volume) - 1。DuckDB 窗口帧在分区边界截断，SQL 加 `count(*)=20` 满帧守卫对齐 ORM fail-closed。
- `beta_60`：60 收益率对市场收益的总体矩 beta（`cov/var`，窗口不满或任一收益缺失即 NULL）。市场收益 = **等权 stock/etf 日收益均值，指数剔除**（指数是工具不是市场组合成员；这是温层显式口径，与 ORM helper「按运行宇宙计算」不同，已写进脚本文档字符串）。
- `residual_volatility_60`：同窗残差 `ret - beta*mkt_ret` 的总体标准差，用窗口和的代数展开单次窗口扫描实现。
- `turnover_percentile_120` 不可回填：curated `prices_qfq` 的 `turnover_rate` 全 NULL（10,668,757/10,668,757），需先重建带换手历史的 curated release。
- 正确性测试：合成 Parquet + 纯 Python 镜像 ORM 语义对照（`tests/test_factor_scripts.py`），含 beta 代数、21 bar/61 bar 预热 fail-closed、指数不进市场收益三条断言。
- 发布：`20261024-selfevo3ext16`（2024-05~2026-04，916 只 × 484 日）与 `20261024-selfevo-seg2022-ext16`（2022-05~2024-04，949 只 × 485 日），父 release 直接挂 `20261021-1800`（读侧父回退只有一层，子 release 必须一次带全 16 因子；13 个旧因子由同一确定性 SQL 重算，宇宙与剔除集与原 release 一致）。
- 先验方向：`residual_volatility_60` 用平台注册默认（越低越好）；`abnormal_volume_pressure`（量能压力取高）与 `beta_60`（低 beta 异象先验）为显式撰写，非全样本 IC 知识；≥60 开市日训练窗允许按证据翻转。

## seg2022 段对照（2022-05~2024-04，t120/60/60，top20，20 日调仓，组合约束，a_share_cost_v1）

| 配置 | 窗数 | 正窗 | 均值/窗 | 最差单窗 | 最差回撤 |
| --- | --- | --- | --- | --- | --- |
| 静态 `ic_aligned`（7 因子手工方向） | 6 | 3/6 | -0.23% | -12.99% | -28.85% |
| `training_ic_v1` 13 因子池（min_survivors=2 事后放宽） | 6 | 3/6 | -0.13% | -10.99% | -25.75% |
| **`training_ic_v1` 16 因子池（默认 min_survivors=3）** | 6 | 3/6 | **+2.43%** | -9.68% | **-17.95%** |

扩池序列逐窗收益：+16.50% / -9.68% / +11.58% / -4.49% / +2.73% / -2.07%（run #57-62）。

逐窗选中集合（config hash `ca4a1e12`，全部 6 窗覆盖下限 3 成立）：

- `residual_volatility_60` 进入 **6/6** 窗（方向一律越低越好）；`abnormal_volume_pressure` 进入 **4/6** 窗（方向翻转为越低越好）。窗口 3 仅 3 个存活（`volume_stability_20` + `residual_volatility_60` + `volatility_5`）——**没有新因子该窗会再次 fail-closed**。
- 方向翻转普遍（momentum/volatility 族先验取高、证据翻转为低），符合 120 日窗允许翻转的设计；低波动方向占优与该下跌段一致。

## 2024-2026 段：fail-closed 拒绝即结论

窗口 0（训练 2024-05-06~2024-10-30）16 候选仅 2 存活，低于覆盖下限 3，整次运行拒绝（错误信息携带逐因子原因表）：

- 存活：`abnormal_volume_pressure`（flipped）、`volume_stability_20`（maintained）。
- `residual_volatility_60` |IR|=0.204、`momentum_20` |IR|=0.283 等，全部低于 0.30 门槛；`beta_60` mean RankIC≈0.001 直接出局。

**价量族的证据强度上限就在这里**：扩池解决「选得出」（seg2022），解决不了「2023-07 之后价量因子普遍失效」。该段要过预注册门槛，必须引入与价量低相关的财务/估值/情绪因子历史序列——即 P2-4（历史 universe）/P2-5（估值历史）数据项。

## 有效性限制（沿用首次实跑 + 新增）

1. 沿用：公司行为事件会计未实现（universe 剔除窗口内公司行为证券）、幸存者偏差未修正、行业标签为当前值回填、停牌事件表为空、单段 6 窗不复利、`min_survivors` 与阈值均为预注册人为设定。
2. 新增：新因子市场收益口径为等权 stock/etf（指数剔除），与 `factor.factor_profile` 的 LEAD 口径、ORM helper 的运行宇宙口径均不可直接比数值；beta/残差因子每段前 10 个交易日无值（61 bar 预热），只影响窗口 0 训练段（选择器按候选剔除并记录），测试窗从第 120 日起无缺口。
3. 收益数字全部用于**机制验证与偏差说明**，不得表述为可信策略收益或可交易预期。

## 下一步（按 ROI 排序）

1. **P2-5 估值历史序列**（数据侧）：`valuation_snapshot` 目前仅 3 个交易日，价值/质量因子无法做历史 IC；东财估值手工补采已支持指定历史交易日，先补齐与价量窗口重叠的估值历史，把 PE/PB/ROE 族加入候选池。
2. 自演进阶段 2-3：有界网格 challenger 生成器 + champion/challenger 晋级（本切片的 selector 即其内核）。
3. 跨段统一 universe 后串长期 walk-forward。
