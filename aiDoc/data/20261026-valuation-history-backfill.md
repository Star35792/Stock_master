# 估值历史回填（2026-10-26）

本文记录本轮「数据补齐」中**估值历史序列**的采集、发布与消费，以及每个字段的**时点（PIT）有效性**判定和**偏差方向**登记。

## 1. 背景与目标

`market.valuation_snapshot` 在控制面只保存了实际采集过的少数交易日，因此任何价值类因子都没有足够长的历史可以评估。此前 `factor.factor_profile` 只有 7 个价量因子的 IC 记录（`v2-qfq-2y`，484 个交易日），估值族因子从未有过逐日 IC，价值因子因此无法进入因子池分级。

供应商的估值分析表 `RPT_VALUEANALYSIS_DET` 覆盖 2018-01-02 起的逐日估值，且其收盘价与本项目的可信日线**完全一致**（实测 8/8 交易日零误差），因此可以用作研究层的估值历史来源。

采集范围经确认：**2024-05-01 起**（与既有 `v2-qfq-2y` IC 证据窗口对齐）。

## 2. 数据落点

| 项 | 事实 |
|---|---|
| 表 | `research.valuation_history` |
| 物理位置 | 温层 Parquet：`backend/data/warm/exported/<release_tag>/research/valuation_history/` |
| 控制面 | **没有**该表（不出现在控制面 `information_schema`） |
| ORM 模型 | **没有**（不参与 `Base.metadata.create_all`） |
| Alembic 迁移 | **没有** |
| 读取入口 | `WarmResearchReadRepository.list_valuation_history` / `valuation_history_coverage` |

判定标准见 `aiDoc/modules/schema-plan.md` §17。

放在 `research` 而不是 `market` 命名空间，是因为这些行是**今天采集的历史数据**，不是当时可知的证据。`aiDoc/data/source-ingestion-policy.md` §13 禁止把历史补采发布到 `market` 层，本轮遵守该约束。

## 3. 字段契约

| 列 | 类型 | 来源字段 | 含义 |
|---|---|---|---|
| `symbol` | VARCHAR | `SECUCODE` | 证券代码（带交易所后缀） |
| `trade_date` | DATE | `TRADE_DATE` | 交易日 |
| `close` | DECIMAL(18,4) | `CLOSE_PRICE` | 未复权收盘价 |
| `pe_ttm` | DECIMAL(24,10) | `PE_TTM` | 滚动市盈率 |
| `pb_mrq` | DECIMAL(24,10) | `PB_MRQ` | 市净率（最近报告期） |
| `ps_ttm` | DECIMAL(24,10) | `PS_TTM` | 滚动市销率 |
| `total_market_cap` | DECIMAL(30,6) | `TOTAL_MARKET_CAP` | 总市值 |
| `total_shares` | DECIMAL(30,6) | `TOTAL_SHARES` | 总股本 |
| `free_shares_a` | DECIMAL(30,6) | `FREE_SHARES_A` | 流通股本 |
| `float_market_cap` | DECIMAL(30,6) | `NOTLIMITED_MARKETCAP_A` | 流通市值 |
| `change_rate` | DECIMAL(18,8) | `CHANGE_RATE` | 当日涨跌幅 |
| `pit_verified` | BOOLEAN | — | 恒为 `false`（见 §4） |
| `source_name` | VARCHAR | — | 恒为 `eastmoney` |
| `observed_at` | VARCHAR | — | 采集时刻（UTC ISO8601） |

**精度**：全部经 `Decimal(str(value))` 转换，不允许二进制浮点中转。写入 Parquet 时使用带引号的字符串字面量，让 DuckDB 直接解析进 DECIMAL 列，避免浮点截断。

**刻意不取的字段**：`SECURITY_NAME_ABBR`（证券简称）与 `BOARD_NAME`（行业板块）。实测这两个字段在 2018 与 2026 的全样本上**零变化**，即供应商在所有历史行上返回的是**当前值**；取回来就是静默前视。ST 历史与行业时点因此不可得，见 §7。

## 4. 字段级 PIT 有效性判定

这是本轮最关键的产出。每个字段的有效性独立判定，不能因为整张表都是"晚采"就把所有字段一视同仁。

| 字段 | PIT 判定 | 依据 | 偏差方向 | 可否用于因子证据 |
|---|---|---|---|---|
| `close` | **真 PIT** | 与可信日线逐笔零误差（8/8 交易日 mismatch=0） | 无 | 可 |
| `pe_ttm` | **真 PIT** | 按**披露日**切换，即 TTM 口径随新财报披露而更新，切换点早于报告期末 | 无 | 可（但需注意这是"披露日口径"而非"报告期末口径"） |
| `pb_mrq` | **前视** | 按**报告期末**切换，最早在报告期末当天即为真值，而披露通常滞后 28~120 天 | **向上偏乐观**（用了尚未披露的净资产） | **不可**。可采集、可展示，不可作为可交易证据 |
| `ps_ttm` | 同 `pe_ttm` | 按披露日切换 | 无 | 可 |
| `total_market_cap` | **真 PIT** | 随行情逐日变化的时点量 | 无 | 可 |
| `total_shares` | **真 PIT** | 股本变更时点量 | 无 | 可 |
| `free_shares_a` | **真 PIT** | 流通股本变更时点量 | 无 | 可 |
| `float_market_cap` | **真 PIT** | 随行情逐日变化 | 无 | 可 |
| `change_rate` | **真 PIT** | 当日行情派生 | 无 | 可 |
| 整行 | **晚采** | 所有行都是今天对历史日期的观测 | 中性（但价格序列本身无偏） | 结论：IC 是**研究证据**，不是可交易证据 |

### 4.1 偏差方向登记表

「偏差方向」指该字段在历史回溯使用时相对"当时可知"的方向性偏离：

| 偏差类型 | 涉及字段 | 方向 | 后果 |
|---|---|---|---|
| 前视（look-ahead） | `pb_mrq` | 用未披露的净资产 → PB 偏低 → 显得"更便宜" | 价值因子被系统性高估，多头组合收益虚高 |
| 当前值回填 | `SECURITY_NAME_ABBR`、`BOARD_NAME` | 用今天的名称/行业标注历史 | ST 历史与行业时点不可得（已证伪，字段直接不取） |
| 幸存者偏差 | 整表 | 只覆盖今天仍在交易的标的，退市整理期证券缺失 | 退市崩盘样本缺失，极端收益被低估 |
| 晚采但中性 | 其余字段 | 数值本身无偏，但"当时是否可得"不可证 | 只能作为研究证据，不得表述为可交易 |

### 4.2 为什么 `pb_mrq` 仍然采集

不采集会让"估值序列"缺一半，且未来拿到带披露日的字段时要重新回填整段历史。采集但**在数据模型、IC 脚本和文档三处同时标记**为不可用，是成本更低、更诚实的做法：`pb_mrq` 列存在、`pit_verified=false`、IC 脚本产出 `value_pb_mrq` 因子时在 caveats 中显式声明前视。

## 5. 采集链路

```
东财 RPT_VALUEANALYSIS_DET
  → app/data_sources/eastmoney.py :: fetch_valuation_history_page
  → app/services/valuation_history_collection.py :: ValuationHistoryCollector
      ├─ iter_pages()      并发分页抓取（按页号排序产出，保证时间序列连贯）
      ├─ build_rows()      规范化 + Decimal 精度保持
      ├─ write_parquet()   单条大 SQL 写入（0.35 s/5,000 行，无临时文件）
      ├─ evaluate_gates_in_sql()  双闸门（SQL 内 JOIN，不经 Python 中转）
      └─ publish_accepted_pages() 从暂存页按通过日过滤发布
  → scripts/ingest/eastmoney/collect_valuation_history.py
  → 温层 release（原子发布：staging → os.replace → exported/）
```

### 5.1 分页排序（本轮修掉的重大缺陷）

供应商的分页必须用**复合排序**：

```
sortColumns = "TRADE_DATE,SECURITY_CODE"
sortTypes   = "1,1"
```

只按 `TRADE_DATE` 排序时，服务端对**同一交易日内部**的行序不作保证。实测 2026-10-01~10-19 的分页：第 1 页与第 2 页之间**重复 571 个 `(symbol, trade_date)` 键**，同时同量丢行。此前观察到的"覆盖率 0.907 低于 0.95 门槛"完全是分页错乱造成的伪影，不是供应商数据缺失。

修复后：跨页重复归零，8/8 交易日全部过闸门，44,594 行零拒绝。

> 注：供应商对 `sortTypes` 的参数格式有严格要求——`sortTypes=1` 会报"排序字段和顺序数量不一致"，`sortTypes=["1","1"]` 会报"排序顺序字段不能为非数字类型"，只有 `"1,1"` 可用。

### 5.2 双闸门（fail-closed）

一个交易日只有同时满足两条才被发布：

| 闸门 | 判据 | 口径 |
|---|---|---|
| 价格一致 | 供应商收盘价与可信日线逐笔比对，容差 `max(0.001, \|close\| × 0.001)`，**零容忍** | 任一标的不符即拒收该日 |
| 股票池覆盖 | 供应商覆盖的个股数 ÷ 可信个股数 ≥ 0.95 | 分母**只含 `security_type='stock'`**（原含 569 个指数 bar，供应商只提供个股，已修正） |

另加**重复键闸门**：同一 `(symbol, trade_date)` 出现多次即拒收该日。

### 5.3 控制面证据

`raw.vendor_snapshot` 每页只落**元数据**：请求参数、响应 SHA256（`row_hash`）、行数、响应字节数、抓取时刻。明细留在 Parquet。

早期版本把完整响应体（1.6 MB/页）写进控制面，一次中断的采集就把 `stock_master.duckdb` 从 726 MB 撑到 1 GB。现已剥离（保留 SHA256，审计能力不变），数据库回到 697 MiB。

## 6. 消费路径

```
research.valuation_history (温层 Parquet)
  → WarmResearchReadRepository.list_valuation_history()
  → scripts/factors/compute_value_factor_ic.py
      → value_earnings_yield / value_pb_mrq
      → 逐日 IC / RankIC + 端点价差 + 分位单调性 + 顶分位换手
  → factor.factor_profile（upsert，幂等）
  → FactorBatchProfilerService.summarize()  聚合 IC + 端点 + PIT 判定
  → FactorPoolClassifierService 分级（IC 闸门 + 端点闸门 + PIT 闸门 + 生命周期闸门）
```

### 6.1 ORM 孪生路径

按 `aiDoc/modules/backend-layer-rules.md`，温层只读仓库与 ORM 仓库必须同批次实现。ORM 孪生是 `app/repositories/research_valuation.py` 的 `ResearchValuationReadRepository`，读控制面的 `market.valuation_snapshot`。

两条路径的**列名、排序、过滤语义一致**，但有两处**故意的不等价**，且都由测试固定：

| 字段 | 温层 | ORM 孪生 | 原因 |
|---|---|---|---|
| `pb_mrq` | 有值 | **`None`** | 控制面的 `pb` 从未记录切换规则，冒充同一字段会掩盖前视 |
| `pit_verified` | `false` | `true` | 控制面是当日实采快照，温层是历史回溯 |

覆盖范围也不同：控制面只有实际采集过的交易日，温层才有完整回填。需要历史必须走温层。

### 6.2 价值因子方向

两个在用因子都表达成**值越大 = 越便宜**：

| 因子 | 定义 | 版本 | PIT 判定 |
|---|---|---|---|
| `value_earnings_yield` | `1 / PE_TTM`（仅正 PE） | `v2-valhist` | `point_in_time` |
| `value_pb_mrq` | `-PB_MRQ` | `v2-valhist` | **`look_ahead`** |

因此 **IC 为正 = 便宜的涨了**，符号可解释。横截面少于 50 个观测的交易日直接跳过，不产出无意义的相关系数。

#### 6.2.1 `value_pe_ttm` 已退役

早期版本还发布过 `value_pe_ttm = -PE_TTM`，**方向性错误**，本轮退役：

* `PE_TTM` 在亏损股为负（样本 26.51%：2024/2025/2026 分别为 23.93%/26.93%/28.15%）。`-PE_TTM` 把深度亏损公司映射成**大正数**，在"值越大越便宜"的约定下，**最便宜五分位 100% 是亏损股**（645,112 行）。该因子度量的是"亏得有多狠"，不是"有多便宜"。
* 限定 `PE_TTM > 0` 也不会产生新因子：在该子集上 `-PE_TTM` 与 `1/PE_TTM` 是**同一个单调变换**，595/595 个交易日的 Spearman 秩相关恰为 **1.000**。同时保留会重复暴露。
* 端点证据印证了方向错误：退役定义在 20 日多空价差上是 **+44.3 bp（t = +4.49）**，纯粹因为亏损小盘股在这段窗口上涨；修正后的定义是 **−69.3 bp（t = −2.70）**。

退役证据保留在 `v1-valhist` 供审计，因子定义的 `status='retired'`，分级器**硬冻结**。修正后的证据发布到 **`v2-valhist`**，与退役行分属不同版本。

#### 6.2.2 端点闸门：IC 不等于可交易

秩 IC 只说明"因子值与未来收益单调相关"，不说明"极端分位可交易"。本轮为此在因子池分级器中引入**端点闸门**：进入核心池要求 **1 日多空（top−bottom 分位）价差严格为正**。

实测（`v2-valhist`，595 个交易日，2024-05-06 ~ 2026-10-16，已排除金融行业）：

| 因子 | RankIC 1d | RankIC 5d | RankIC 20d | 多空 1d | 多空 5d | 多空 20d | 分位单调性 | 顶分位换手 | 覆盖率 |
|---|---|---|---|---|---|---|---|---|---|
| `value_earnings_yield` | +0.018720 | +0.025255 | +0.020143 | **−0.000122** | **−0.000836** | **−0.006855** | −0.037095 | 0.016036 | 0.6957 |
| `value_pb_mrq` | +0.032484 | +0.047515 | +0.059168 | +0.000343 | +0.001497 | +0.000951 | +0.014331 | 0.016534 | 0.9502 |

两个因子的三个 horizon 秩 IC 全为正，但：

* `value_earnings_yield` 的**端点全为负**（20 日 −68.55 bp）→ 端点闸门拦截。分位单调性为负（−0.0371）也印证了它的**倒 U 形**结构：最贵的一端拖累收益。它同时因覆盖率 0.6957 低于通用阈值 0.70 而落入冻结池——0.6957 是「盈利股占全市场比例」的如实度量（约 30% 的 A 股亏损或 PE 无意义），是定义上的边界而不是数据缺陷。
* `value_pb_mrq` 端点为正，但 `PB_MRQ` 按报告期末切换、前视 28~120 天 → **PIT 硬门槛**拦截，硬冻结。

换手率 1.6%/日（≈33%/年）是 PE 价值因子的真实稳定度：最低 PE 五分位的成分股在日频重排下变化很慢（实测相邻两日交集 777/787）。

#### 6.2.2.1 金融行业排除

通用价值因子**排除银行、保险、券商等金融行业**（`app/services/financial_sector.py::is_financial_industry`，实测 122 只个股）：

* 金融企业的资产负债表就是商业模式本身，`PB` / `PE` 的含义与工业企业完全不同；
* 更关键的是金融板块**天然便宜**（银行长期低 PB 低 PE），留在横截面里会让这 122 只标的**主导「最便宜」分位**，因子会退化成行业押注而非价值因子。

排除后端点与 IC 结论不变（`value_earnings_yield` 端点仍为负、`value_pb_mrq` 端点仍为正），说明结论对该排除不敏感；但排除是金融正确性的**必要条件**，不能因为"结论没变"而省掉。

> 行业标签取自控制面 `market.security.industry`，即**当前标签**——项目没有历史行业时点表（`market.security_industry_history` 仍为 0 行），因此该排除本身是当前标签近似，已在脚本 caveats 与落盘 payload 中声明。

#### 6.2.3 覆盖率口径修正

`coverage_rate` 的**分子分母都取因子自身非空集**，导致 `value_earnings_yield` 显示 0.9964~1.0000，看不出亏损股被排除的 26% 宇宙。修正为「分子=因子非空观测数，分母=当日价格宇宙」后：

| 版本 | `value_earnings_yield` 覆盖率 |
|---|---|
| `v1-valhist`（旧口径） | 0.9984 |
| `v2-valhist`（修正后，含金融行业） | 0.7166 |
| `v2-valhist`（修正后，已排除金融行业） | **0.6957** |

真实可交易宇宙占比约 69.6%——盈利股占全市场不到七成，约 30% 的 A 股亏损或 PE 无意义。这一修正同步应用到温层 IC 脚本（`compute_warm_factor_ic.py`），修正后温层因子覆盖率为 0.9950~1.0000。

> 若将来要把盈利股族因子纳入观察池，正确的做法是给该族单独设定覆盖率口径（分母改为盈利股宇宙），而不是下调全局阈值——否则所有因子的覆盖率闸门都会被拉低。

#### 6.2.4 分级结果

两道闸门启用后，因子池分级结果从 **core 4 个收敛到 core 1 个**：

| 池 | 数量 | 因子 |
|---|---|---|
| **core** | 1 | `volume_stability_20@v2-qfq-2y` |
| observation | 1 | `max_drawdown_20@v2-qfq-2y`（端点为负） |
| frozen | 17 | 含 `value_pb_mrq@v1-valhist` / `v2-valhist`（PIT 前视）、`value_earnings_yield@v1-valhist` / `v2-valhist`（端点为负 + 覆盖率不足）、`value_pe_ttm@v1-valhist`（已退役）、`momentum_*` / `volatility_20` / `liquidity_20` / `downside_volatility_20`（秩 IC 为负）、`v1-qfq` 族（样本 119 日 < 200） |

被拦下的是两个曾经的核心成员：

* `max_drawdown_20@v2-qfq-2y`：三 horizon 秩 IC 全正（+0.017893 / +0.022586 / +0.018861），但 1 日端点 **−0.000519** → 端点闸门拦截。
* `value_earnings_yield`：端点全负（见 §6.2.2），且覆盖率 0.6957 低于 0.70。

唯一幸存者 `volume_stability_20@v2-qfq-2y` 的端点证据最强：多空 1d **+0.000947** / 5d +0.003953 / 20d +0.004179，分位单调性 +0.121，RankIC 1d +0.043498（IR 0.602，胜率 72.5%）。

### 6.3 时点性闸门（PIT，fail-closed）

`factor_definition` 新增 `pit_status` 列，取值：

| 值 | 含义 | 分级后果 |
|---|---|---|
| `point_in_time` | 输入在交易日当天即已知 | 可进核心池 |
| `look_ahead` | 至少一项输入泄漏未来信息 | **硬冻结**，任何池都不得进 |
| `unknown` | 尚未审计（**默认值**） | **不得进核心池**，可进观察池 |

`unknown` 是默认值，因此一个从未被审计的因子不会悄悄进入核心池。本轮已完成审计的只有：7 个温层价格量因子（纯历史价格/成交量，天然时点安全）与 3 个价值因子。其余因子家族保持 `unknown`。

### 6.4 生命周期闸门

`factor_definition` 另增 `status` 列（`active` / `retired`）。`retired` 表示该定义已被取代或已知有误：证据保留供审计，但因子**硬冻结**。`v1-valhist` 的三个价值因子定义全部标为 `retired`。

> 注意与 `is_active` 的区别：`is_active` 表示"是否参与批量画像"，`status` 表示"这个定义是否还是活的证据"。价值因子的证据由专用脚本产出，因此 `is_active=False` 但 `status='active'`；温层价格量因子同理。

### 6.5 幂等落库

两个 IC 脚本此前用 `session.merge(FactorProfile(...))`——ORM 对象不带主键时 `merge` 退化为 **INSERT**，重跑会撞 `(factor_name, factor_version, trade_date)` 唯一约束，**修正后的证据写不进去**。本轮改为 `FactorProfileRepository.upsert()`（`ON CONFLICT DO UPDATE`），并实测重跑后总行数不变（7,169 行）。

### 6.6 初筛 ST 阻断解除的直接验证

`special_treatment_status` 曾**没有任何数据源写入**而恒为 NULL，初筛把「未知」当「受限」，连续 83 次运行 0 个可复核候选。本轮由证券简称派生并回填控制面，验证结果：

| 项目 | 数值 |
|---|---|
| `market.security`（`security_type='stock'`） | `normal` 5,383 / `ST` 200 / `delisting` 1 |
| `market.daily_bar`（2026-09-24） | `normal` 5,361 / `ST` 196 |
| 初筛 `ready_count`（2026-09-24，完整数据日） | **3,770**（此前连续 83 次为 0） |
| `treatment_unknown_or_restricted` 剩余原因数 | 196（真实 ST/*ST/退市整理，不再是全市场） |
| 非个股（指数/概念）ST 状态 | 恒为 NULL——派生只对 `security_type='stock'` 生效，`ST板块` 是板块指数不是个股 ST |

**残留阻断（与本轮无关，属数据新鲜度）**：按「今天」跑初筛时 `ready_count` 仍为 0，但原因不是 ST，而是目标交易日无数据：

- 控制面 `market.daily_bar` 最新仅到 **2026-10-21**，且该日只有 **1** 条 `quality_status='trusted'`；
- `market.valuation_snapshot` 仅覆盖 **2026-09-18 ~ 2026-09-24**（3 个交易日）；
- 目标日的 input preparation batch 不存在。

即 `trusted_bars_missing_for_target` / `valuation_missing_for_target` / `roe_reported_missing_for_target` / `input_preparation_missing_for_target` 四条**全局**原因覆盖全部 5,584 只个股，ST 分支根本没被走到。恢复日线/估值日常供给后，同一条路径会直接产出候选。


## 7. 明确不可得、继续挂账的项

| 项 | 状态 | 原因 |
|---|---|---|
| ST 历史时点 | **不可得** | 供应商历史行的证券简称是**当前值**（2018 vs 2026 名称变化数 = 0），倒填即前视 |
| 行业时点历史 | **不可得** | 同上，`BOARD_NAME` 2018 vs 2026 变化数 = 0 |
| 退市证券价格 | **缺失** | 温层缺 15/17 只退市证券的可信价格；退市整理期 −59%~−96% 的崩盘不在可信 bar 内，扩股票池零效果 |
| 公司行为事件会计 | 未实现 | 与本轮无关，另立切片 |

## 8. 与项目边界的关系

本数据集服务于**选股研究与持仓管理**。它不改变项目的终态定位：不连接券商、不做实盘自动化交易、不发送真实订单。价值因子的 IC 是研究结论的输入，不是交易信号。

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `backend/app/data_sources/eastmoney.py` | 供应商适配器（复合排序） |
| `backend/app/services/valuation_history_collection.py` | 采集服务（并发、精度、闸门、发布） |
| `backend/scripts/ingest/eastmoney/collect_valuation_history.py` | 执行脚本 |
| `backend/app/repositories/research_read.py` | 温层读取（`list_valuation_history`） |
| `backend/app/repositories/research_valuation.py` | ORM 孪生读取 |
| `backend/app/factors/evidence_catalog.py` | IC 证据因子目录注册（`pit_status` / `status` 标注） |
| `backend/app/services/factor_pool_classifier.py` | 四道闸门的分级器 |
| `backend/app/services/financial_sector.py` | 金融行业判定（价值因子排除用） |
| `backend/scripts/factors/compute_value_factor_ic.py` | 价值因子 IC 计算（含金融行业排除） |
| `backend/scripts/factors/compute_warm_factor_ic.py` | 温层因子 IC 计算（幂等 upsert） |
| `backend/scripts/factors/classify_factor_pool.py` | 因子池分级 CLI |
| `backend/tests/test_valuation_history_collection.py` | 采集规则测试 |
| `backend/tests/test_valuation_history_research_read.py` | 温层读取测试 |
| `backend/tests/test_research_valuation_repository.py` | 孪生一致性测试 |
| `backend/tests/test_evidence_catalog.py` | 证据目录注册测试 |
| `backend/tests/test_factor_pool_classifier.py` | 四道闸门测试 |
| `backend/tests/test_value_factor_ic_script.py` | 价值因子脚本测试（金融行业排除 / 端点 / 退役） |
| `backend/tests/test_warm_layer_release_resolution.py` | 温层 release 解析与列集对齐回归测试 |
| `backend/scripts/maintenance/shrink_control_plane.py` | 控制面证据瘦身（修复早期版本遗留） |

落盘证据表：

| 文件 | 内容 |
|---|---|
| `aiDoc/data/20261026-value-factor-ic-v2-valhist.json` | 价值因子 IC + 端点聚合 |
| `aiDoc/data/20261026-warm-factor-ic-v2-qfq-2y.json` | 温层价格量因子 IC + 端点聚合 |
