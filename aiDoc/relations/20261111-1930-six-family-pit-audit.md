---
doc_type: evidence
status: current
as_of: 2026-11-11
note: >-
  六族均为另类数据/情绪族因子，与行业时点无关；审计台账仍是当前依据，文中测试基线数字（1,430 passed）是当日快照。
---
# 六族因子时点性审计：把「凭什么算 PIT」写成代码

> 日期：2026-11-11
> 关联任务：#11 六族 alternative 数据断供修复（第三阶段：PIT 认证）
> 状态：已交付并验证（34 条审计台账落库 + 全量 1,430 passed）
> **2026-11-11 修订**：炸板池端点接通后（见
> `20261111-2050-alternative-history-backfill.md`），`limit_up_failure_rate` 与
> `overheat_reversal_signal` 由 `unknown` 改判 `point_in_time`，
> 判定分布更新为 **17 / 3 / 14**。

## 一句话

六族因子的 `pit_status` 此前**全部**是 `unknown`，于是分级器对它们一律
fail-closed——数据补齐了也进不了核心池。本轮把「凭什么算 point_in_time」
写进代码（`app/factors/pit_audit_catalog.py`，34 条逐因子带依据），并顺带
修掉一个会**静默抹掉**审计结论的底层缺陷。但结论必须说清楚：**认证只解除
一道闸门，真正卡住六族因子的是数据历史长度，不是认证。**

## 一、三态语义：`look_ahead` 与 `unknown` 不是同一件事

`pit_status` 是因子池分级器的硬门槛，三种取值各自含义不同：

| 状态 | 含义 | 能否进核心池 | 能否进观察池 |
|---|---|---|---|
| `point_in_time` | 输入在目标交易日之前即已知 | 可以（还需过其他闸门） | 可以 |
| `look_ahead` | **已判定有毒**——至少一项输入泄漏未来信息 | 不可 | **不可**（硬冻结） |
| `unknown` | **尚未判定**——没做完审计 | 不可 | 可以 |

把后两者混为一谈是常见的错误：`look_ahead` 是「查出来了有毒」，`unknown`
是「还没查」。前者任何池都进不去，后者只挡核心池、观察池仍可留。

## 二、审计依据只有两类

台账里每条 `PitAuditEntry` 都必须回答「凭什么」，依据只承认两类：

1. **数据语义** —— 快照记的是什么日期、什么时候公布；
2. **消费规则** —— 因子取值时是否强制 `snapshot_date < 目标交易日`。

没有依据的因子一律留在 `unknown`，**不靠「目前没发现问题」来放行**。
（本轮所有 `point_in_time` 判定的消费规则都落到同一句话：因子取值只接受
`snapshot_date < 目标交易日的快照行`。）

## 三、判定结果：17 / 3 / 14

> 下表是**初判**（第三阶段落库时的状态）。炸板池端点接通后，
> `limit_up_failure_rate` 与 `overheat_reversal_signal` 已改判 `point_in_time`，
> 分布更新为 **17 point_in_time / 3 look_ahead / 14 unknown**。

### point_in_time（15 个）

* **情绪族 9 个**：`hot_rank_score` / `hot_heat` / `hot_rank_change` /
  `limit_up_strength` / `limit_up_breadth` / `dragon_tiger_net_buy_ratio` /
  `consecutive_limit_up_height` / `market_sentiment_score` /
  `sentiment_temperature_percentile`
  —— 依据：热股榜 / 涨停池 / 火箭榜都是**收盘后快照**，`snapshot_date` = 该榜
  对应交易日；连板梯队端点自带历史序列，每行即该梯队对应交易日。
  龙虎榜是 T 日交易、**T+1 公布**，取用时恰好在公布日当天，不前视。
* **资金流族 5 个**：`main_fund_net_inflow` / `main_fund_net_inflow_ratio` /
  `main_fund_rank_score` / `main_fund_inflow_change_5` /
  `main_fund_inflow_percentile_20`
  —— 依据：东财主力资金榜收盘后发布。
* **拥挤度 1 个**：`crowding_score` —— 依据：三个成分因子本身时点安全
  （换手率分位 / 异常成交量压力只用当日及历史价量，市场情绪分位只用收盘后
  快照），按成分因子的 `trade_date` 对齐，不做二次时间平移。

### look_ahead（3 个，硬冻结）

`earnings_revision_1m` / `earnings_revision_breadth` / `forecast_dispersion`

依据：输入是**分析师一致预期**——前瞻性预测，没有首次可得时间。按项目 PIT
铁律，一致预期 / 评级 / 研报一律不入 market 闸门，故硬冻结。

> 注意判定是按**输入**而不是按单个因子：台账里只要 `datasets` 含
> `earnings_revision`，就不可能是 `point_in_time`（测试里有这条断言）。

### unknown（16 个，fail-closed）

| 类别 | 因子 | 为什么不能放行 |
|---|---|---|
| 从未采集 | `rate_trend_10y` / `yield_curve_slope` / `social_financing_impulse` / `m1_m2_gap` / `usd_cnh_trend` / `commodity_inflation_proxy` / `credit_spread_change` / `policy_event_score` / `margin_financing_change_20` / `etf_flow_industry_20` / `institutional_crowding_score` | 没有任何记录，**无法验证 as-of 语义** |
| 上游断供 | `northbound_flow_ratio` | 北向资金自 2024-08 断供（权威口径须 HKEX），既无数据也无法验证 |
| 端点未找到 | `limit_up_failure_rate` / `overheat_reversal_signal` | ~~炸板池 `failed_limit_up` 至今没找到端点~~ **→ 已推翻：端点一直都在，见第四阶段文档** |
| 无公告日字段 | `guidance_surprise` / `profit_warning_risk` | 业绩预告 / 预警是**公司公告**，原则上公告日即可得日、PIT 可控；但本项目既无采集实现也无公告日字段，落库记的是哪个日期无法审计 |

宏观族另有一层更隐蔽的坑：**发布滞后口径未确认**。社融 / M1 / M2 在次月
10–15 日才发布，若 `snapshot_date` 记的是**观测期**而不是公布日，那么
「`snapshot_date < 目标交易日`」这条消费规则本身就是前视。口径没查清之前
一律 fail-closed。

## 四、顺带修掉的底层缺陷：`ensure()` 会抹掉审计结论

审计落库后实测发现：**因子服务每次运行都会重新登记自己的定义行**，而
`FactorDefinitionRepository.ensure()` 的 `pit_status` 默认值是 `"unknown"`，
于是每跑一次因子服务，审计结论就被**静默写回 unknown**。

这解释了此前一个一直说不通的现象：价格量因子 `v1-qfq` 判 `unknown`，而
`v1-none` / `v2-qfq-2y` 是 `point_in_time` ——差别不在因子本身，而在**谁最后
写了这行**。

修法（`app/repositories/factor/factor_definition.py`）：

```python
pit_status: str | None = None,     # None 表示保留既有值
...
if pit_status is None:
    pit_status = ((definition.pit_status or "unknown")
                  if definition is not None else "unknown")
```

新建定义行仍取 fail-closed 默认 `unknown`；显式传值时显式值仍然优先。
实测验证：「服务重新登记后 `pit_status: point_in_time -> point_in_time` 保持」。

> 这是本轮最有价值的发现：**审计结论必须由代码在每次登记后重新盖章**，
> 否则它只是数据库里一个随时会被覆盖的巧合。

## 五、落库与核对

新脚本 `scripts/factors/audit_six_family_pit_status.py`：

```bash
python -m scripts.factors.audit_six_family_pit_status           # 只报告
python -m scripts.factors.audit_six_family_pit_status --apply   # 写入库
```

报告把三件事摆在一起：**审计台账的判定** / **判定依据** / **库里当前的值**，
三者不一致的地方打 `<<< 与库内不一致` 标记。

实测（2026-11-11）：34 条全部有定义行，库内值与台账**逐条一致**，零标记。
其中 `crowding_score` 有 4 个版本（`v1-none` / `v1-qfq` /
`v2-average-rank` / `v2-average-rank-qfq`），全部标 `point_in_time`。

标注接入 `app/factors/registry.py` 的 `register_all_factor_definitions()`，
与既有的 `annotate_price_volume_pit_status()` 同级——**每次因子服务运行
都会重新盖章**，不依赖「谁记得去跑一次审计脚本」。

## 六、诚实结论：认证不是瓶颈，数据历史长度才是

认证解除的是「时点性」这一道闸门。但因子池分级器还有另外三道，实测：

| 卡点 | 实测 | 说明 |
|---|---|---|
| IC 证据 | `factor.factor_profile` 只有 **10 个因子名 / 7,169 行**，六族因子**一条都没有** | 分级器根本看不到它们，连分类都轮不到 |
| 覆盖率 | `market.alternative_data_snapshot` 共 10,251 行 / 60 个 `snapshot_date`，但**只有 `limit_up_ladder` 覆盖 60 天**，其余 dataset 全是 **1–2 天** | 2 个快照日连 RankIC 都算不出来 |
| 端点 | ~~`failed_limit_up` 至今没找到~~ **→ 已解决：端点一直都在，第四阶段已接通** | 两个因子的输入不再缺一半 |

所以**当前不会有任何六族因子进入核心池**，原因不是认证没做完，而是：

* 没有 IC 证据 → 分级器看不见；
* 快照历史太短 → 就算看见了也算不出覆盖率。

好消息是历史长度会自己长：调度已接入 `daily_loop`，六族数据**每天增一个
快照日**。等 `hot_stock` / `limit_up_pool` / `dragon_tiger` 等攒够 60+ 个
交易日，因子服务就能跑出第一批 IC 证据，届时 PIT 认证的结论才有实际作用。

另一条更快的路是**改用端点自带历史序列的 dataset**（连板梯队就是这种，
一采就有 60 天），但那要另做评估，本轮未做。

## 七、改动清单

| 文件 | 改动 |
|---|---|
| `app/factors/pit_audit_catalog.py` | 新增：34 条审计台账 + `annotate_six_family_pit_status()` |
| `app/repositories/factor/factor_definition.py` | `ensure()` 的 `pit_status` 默认改为 `None`（保留既有值） |
| `app/factors/registry.py` | 接入六族标注；更新 docstring 说明 `pit_status` 的三类来源 |
| `scripts/factors/audit_six_family_pit_status.py` | 新增：报告 / 落库脚本 |
| `tests/test_pit_audit_catalog.py` | 新增 17 例 |

## 八、验证

* 新增测试 17 例全绿：台账覆盖六族全部 spec（不多不少 34 个）、依据非空、
  一致预期系必须 `look_ahead`、从未采集族必须 `unknown`、标注幂等、
  只改 `pit_status` 不碰 `is_active`/`status`/`version`、缺定义行要报出来、
  `ensure()` 保留既有值、新行仍 `unknown`。
* **全量 pytest 1,430 passed，零失败零错误**（分两批 553 + 877）。
* `ruff check app tests scripts` 全绿。
* 落库核对：34 条逐条一致，无「与库内不一致」标记。

## 九、遗留

* **IC 证据**：等快照历史积累，或改用自带历史的端点。
  **→ 第四阶段已解决历史来源**：端点多数支持按日期回溯（公共窗口 12 个月），
  历史已一次性回填，下一步是跑出第一批 IC 证据。
* **游资席位专用因子**：`dragon_tiger_hot_money` 数据已可用（35 标的 / 46 行），
  但因子定义、席位口径选择规则、PIT 审计、IC 证据都还没做。
* ~~**`failed_limit_up`**：端点仍未找到。~~ **→ 已解决。**
* **宏观发布滞后口径**：需要确认 `snapshot_date` 记的是观测期还是公布日，
  确认后 macro 族 7 个因子才能从 `unknown` 前进。
