# 温层因子回填与 IC 证据（2026-10-22 首轮）

本文件记录把因子计算与 IC 证据从「控制面热窗口」搬到「Parquet 温层」的回填策略、实测结果和偏差声明。所有数字只描述对应窗口的样本，不构成可对外表述的策略收益。

## 为什么需要这条链路

控制面 `market.daily_bar` 经瘦身只保留约 59 个交易日热窗口，而 `FactorPoolClassifierService` 要求 `min_trade_dates=200`。走 ORM 的批量诊断永远达不到这个阈值，所以 `factor.factor_profile` 长期为空表，71 个注册因子被一律判为 `frozen`，分级形同虚设。

## 链路

1. `scripts/factors/backfill_warm_factors.py`：在 DuckDB 中直接对 curated `prices_qfq` Parquet 计算 7 个价量因子，发布为**部分 release**（只含 `factor/factor_value`，其余表通过 `base_release_tag` 从父 release 继承，避免每次复制约 700 MB）。
2. `scripts/factors/compute_warm_factor_ic.py`：对同一 release 计算逐日 IC / RankIC（horizon 1/5/20），聚合并写入 `factor.factor_profile`。远期收益用 **qfq** 价格计算（`close[t+h] / close[t] - 1`），除权除息不会伪装成收益。

因子值以 `DECIMAL(24,10)` 落盘，遵守「核心金融数据不用浮点」的项目规则。

## 2026-11-02 并列值口径修复

`compute_warm_factor_ic.py` 与价值族的 `compute_value_factor_ic.py` 原先用 DuckDB `ntile(5)` 分桶，并用普通 `rank()` 计算 Rank IC。`ntile` 会把处在五分位边界的相同因子值按输入行切入不同桶；普通 `rank()` 给并列组最小秩而不是标准的平均秩。两者都会让端点、单调性、换手或 Rank IC 偏离在线 `factor_cross_section` 的口径。

两个脚本现在共用同一并列值诊断准备逻辑：每个 1/5/20 日持有期先取「因子值 ∩ 该持有期非空远期收益」的共同证券集，再分别对因子值和远期收益使用平均秩并确定端点；top/bottom 按名义 20% 的边界值纳入完整并列组；1 日单调性把完整并列组按位置中点分入五层，允许低离散截面出现空层；top 集合换手使用 `1 - 2*|交集|/(|前集|+|后集|)`，因此并列组扩张和收缩对称。全体同值时 top 与 bottom 都是全体证券，端点价差为 0，单调性为未知。

旧的 `20261026-warm-factor-ic-v2-qfq-2y.json`、价值因子 JSON 及其已落库 `factor_profile` 行保留为旧口径证据，不自动重写。任何因子恢复资格前，必须用新代码对目标 `factor_version` 的完整窗口全量重算，并核验全窗口 `code_version` 一致；禁止把新旧口径日期混合聚合。本修复不改变 PIT、幸存者偏差、公司行为、样本外或可交易性结论。

## 两个因子版本的分工

| 版本 | 窗口 | 交易日 | 证券数 | 是否剔除公司行为证券 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `v1-qfq` | 2025-11-03 ~ 2026-04-30 | 120 | 4,695 | **是**（剔除 606 只，另剔除 380 只行情不完整） | 回测用，必须通过 corporate-action reject 闸门 |
| `v2-qfq-2y` | 2024-05-01 ~ 2026-04-30 | 484 | 5,651 | **否** | IC 证据用 |

IC 计算**不受**回测的 corporate-action reject 闸门约束：qfq 价格已含权息调整，除权除息日不会产生伪收益，因此 IC 窗口不需要剔除分红送转证券，股票池更接近全市场。回测必须剔除，因为执行层没有实现事件会计。

## 实测结果（`v2-qfq-2y`，483 个交易日）

| 因子 | RankIC1 | IR1 | 正相关率 | RankIC5 | RankIC20 | 分级 |
| --- | --- | --- | --- | --- | --- | --- |
| `volume_stability_20` | +0.0435 | 0.602 | 72.5% | +0.0578 | +0.0559 | **core** |
| `max_drawdown_20` | +0.0179 | 0.094 | 54.7% | +0.0226 | +0.0189 | **core** |
| `downside_volatility_20` | -0.0333 | -0.160 | 44.3% | -0.0393 | -0.0441 | frozen |
| `liquidity_20` | -0.0401 | -0.259 | 37.5% | -0.0651 | -0.1028 | frozen |
| `momentum_20` | -0.0505 | -0.329 | 36.0% | -0.0690 | -0.0717 | frozen |
| `momentum_60` | -0.0408 | -0.268 | 40.0% | -0.0662 | -0.0884 | frozen |
| `volatility_20` | -0.0523 | -0.253 | 40.8% | -0.0686 | -0.0693 | frozen |

`v1-qfq`（6 个月窗口）的符号与量级一致，说明方向结论稳定。

关键读法：5 个因子的 IC 显著为负且 IR 绝对值在 0.16~0.33，**不是噪声**——它表示因子定义方向与真实效应相反（这个样本里低波动、低流动性、弱动量反而对应更高远期收益）。当前分级策略只承认正向 IC，所以它们一律 `frozen`；若要启用，必须显式取反方向，而不是放宽阈值。

## 偏差与未修正项

- **幸存者偏差未修正**：`market.security` 7,728 只证券 `is_active` 全为真、`delist_date` 全为空，退市证券不在样本内。
- **无样本外验证**：单窗口，未做滚动或样本外切分。
- **无行业中性化**：`market.security_industry_history` 仍为 0 行。
- **停牌未建模**：`market.security_status_event` 已建表但 0 行，停牌只能靠「当日无 bar」推断，与真实停牌无法区分。`v1-qfq` 因此额外剔除了窗口内任一交易日缺 bar 的证券。
- **远期收益用 qfq，执行用未复权**：两者口径不同，IC 高不等于扣费后能赚到。

## 复现命令

```bash
cd backend
.venv/Scripts/python.exe scripts/factors/backfill_warm_factors.py \
  --start-date 2024-05-01 --end-date 2026-04-30 \
  --factor-version v2-qfq-2y --parent-release 20261021-1800 \
  --release-tag 20261022-ic2y
.venv/Scripts/python.exe scripts/factors/compute_warm_factor_ic.py \
  --start-date 2024-05-01 --end-date 2026-04-30 \
  --factor-version v2-qfq-2y --release-tag 20261022-ic2y \
  --out outputs/warm_factor_ic_2y_summary.json
```

回测对照（注意必须显式指定 `--release-tag 20261022-181648`，否则会解析到只含 `v2-qfq-2y` 的新 release）：

```bash
.venv/Scripts/python.exe scripts/research/run_warm_factor_selection.py \
  --spec-set default      # run_id=12
.venv/Scripts/python.exe scripts/research/run_warm_factor_selection.py \
  --spec-set ic_aligned   # run_id=13
```
