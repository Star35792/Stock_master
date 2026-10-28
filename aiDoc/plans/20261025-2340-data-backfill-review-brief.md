# 数据补齐 · 事实基线与候选方案（评审输入）

**编制时间**：2026-10-25
**触发**：用户指令「现在开始数据补齐」
**分级**：T2（新增外部数据源接入、数据契约与血缘、schema 变更、幸存者偏差口径）

---

## 一、当前数据状态（duckdb 直查，已实证）

控制面 `backend/data/stock_master.duckdb`（726 MB）：

| 表 | 行数 | 覆盖 |
|---|---|---|
| `market.daily_bar` | 325,774 | 59 交易日（2026-07-23 ~ 2026-10-21） |
| `market.adjustment_factor` | 1,272,799 | 59 交易日 |
| `market.valuation_snapshot` | 22,248 | **3 个交易日**（2026-09-18 ~ 09-24） |
| `market.index_constituent_snapshot` | 120,343 | **1 个 snapshot_date**（2026-09-18） |
| `market.security` | 7,728 | 全部 `is_active=TRUE`，`delist_date` 全 NULL |
| `market.security_industry_history` | 5,579 | `source=current_label_backfill_20261024`（当前标签回填，非真实历史） |
| `market.security_status_event` | **0** | 已建表未落数 |
| `market.daily_bar_source_compare` | 0 | 多源对账从未落数 |
| `metadata.data_quality_issue` | 0 | 质量问题从未落数 |
| `market.manual_correction` | 0 | 人工修复审计从未落数 |
| `factor.factor_profile` | 4,194 | 已有 IC 证据 |

温层 `backend/data/warm/exported/20261024-selfevo3ext16`（base=`20261021-1800`）：

| 项 | 实测结果 |
|---|---|
| `market.daily_bar` | 10,668,757 行 / **2,483 交易日** / 2016-07-25 ~ 2026-10-19 |
| `market.prices_qfq` | 10,668,757 行 / 2,483 交易日（与 daily_bar 同覆盖） |
| `daily_bar.special_treatment_status` 非空 | 1,972 行，**全部集中在 2026 年**（2016-2025 逐年 0） |
| `daily_bar.is_suspended=TRUE` | **0 行**（全市场无停牌标记） |

**结论**：P0-1（温层历史继承）与 P0-2（`prices_qfq`）经实测**已修复**，读路径拿到 2,483 交易日。`latest_release_tag` 字典序选取的隐患已由 `base_release_tag` 继承机制消除（当前 latest=20261024-selfevo3ext16 → base=20261021-1800 → 完整 2,483 日）。

---

## 二、外部数据源可行性（已实证探测，非推测）

数据源：东方财富 `datacenter-web.eastmoney.com/api/data/v1/get`，报表 `RPT_VALUEANALYSIS_DET`。

### 2.1 关键能力

| 能力 | 实测结论 |
|---|---|
| 历史深度 | **2018-01-02 起**（2016-07-25 返回「返回数据为空」）；单证券 `000001` 共 2,135 条日频记录 |
| 全市场规模 | `(TRADE_DATE>='2018-01-01')` → **9,617,541 行 / 1,924 页**（pageSize=5000） |
| 吞吐 | 4 并发 2,920 行/s；**8 并发 8,389 行/s** → 全量约 19 分钟 |
| 分页上限 | pageSize 可用至 5,000（500→2页/日，5,000→2页/全期） |
| 字段 | `SECURITY_CODE/SECUCODE/TRADE_DATE/CLOSE_PRICE/PE_TTM/PB_MRQ/PS_TTM/TOTAL_MARKET_CAP/TOTAL_SHARES/FREE_SHARES_A/NOTLIMITED_MARKETCAP_A/CHANGE_RATE/SECURITY_NAME_ABBR/BOARD_NAME` |
| 数据完整性 | `PE_TTM` 缺失 0/5,000、`PB_MRQ` 缺失 0/5,000、`BOARD_NAME` 空 0/5,000 |

### 2.2 价格一致性闸门（决定能否进 `market`）

2026-10-19 横截面（5,575 只）对照控制面可信日线（5,563 只，`quality_status='trusted'`、非 suspect）：

```
match=4,988  mismatch=0  not_in_bars=12
```

**零误差**。12 只「东财有、可信 bar 无」的证券，`CHANGE_RATE=0` 且收盘价与前一交易日完全相同 → **停牌**，非缺数。

### 2.3 决定性否定发现（重要）

**`SECURITY_NAME_ABBR` 与 `BOARD_NAME` 是「当前值回填」，不是历史时点值。**

对照 2018-01-02（3,250 只）与 2026-10-23（5,577 只）两个横截面：

```
名称发生变化的证券: 0
行业板块变化的证券: 0
```

已交叉验证：`000010` 在 2018 年的记录上直接显示 `*ST美丽`（2026 年才有的简称）。

**推论**：
- ❌ ST 历史时点 **不可** 从本接口回填（`cym` 曾用名序列有 `深华新A->ST深华新->...->*ST美丽->美丽生态`，但**无日期锚点**，无法定位到具体交易日）。
- ❌ 真实行业时点变更史 **不可** 从本接口回填（`BOARD_NAME` 恒为当前板块）。
- ✅ 但**横截面代码集是真历史**：2018-01-02 存在而 2026-10-23 已消失的 17 只（`国华退`、`*ST康佳A`、`*ST万方`、`*ST奥维`、`恒久退`、`赛隆退`、`天龙退`、`立方退` 等）即为**退市证券**，其退市时点可从「最后出现交易日」确定。

### 2.4 其他已探测接口

| 接口 | 结果 |
|---|---|
| 东财 F10 `CompanySurveyAjax` | 可得曾用名序列 `cym`（**无日期**）与当前行业 `sshy` |
| `RPTA_WEB_XRGL`（退市全名单） | 「报表配置不存在」 |
| `RPT_STOCK_HISTORYMARK` | 155,760 行，但为**诊股评分**，非更名记录 |

---

## 三、候选方案

### 方案 A（推荐）：单次全量估值截面采集，一次派生三个缺口

**一次性拉取 2018-01-02 ~ 2026-10-23 全量估值截面（961 万行）到温层 Parquet**，然后从同一份数据派生：

| 派生 | 目标表/落点 | 解决的缺口 |
|---|---|---|
| 逐日 PE/PB/PS/市值/股本 | 温层 `market.valuation_history`（新 Parquet 表） | **P2-5 估值历史序列**（当前仅 3 天，价值类因子无法做历史 IC） |
| 逐日全市场代码集 | 同上（可派生） | **P2-4 历史 universe**（可作回测 universe 过滤，消除幸存者偏差） |
| 「东财有 bar 无 + 涨跌幅 0 + 收盘价不变」 | 控制面 `market.security_status_event`（已建表 0 行） | **停牌事件**（当前全市场 0 行停牌标记） |
| 证券在截面中的最后出现日 | 控制面 `market.security_status_event`（status=`delisted`） | **P2-2 退市时点（部分）** |

**必须做对的几件事**：
1. **血缘**：`raw` 层留页级证据（URL、参数、页 hash、行数、采集时刻），明细落温层 Parquet。961 万行 JSON 全量进 `raw` 会再撑爆控制面，必须分层。
2. **精度**：价格/市值用 `DECIMAL`，不用 float。
3. **断点续传**：1,924 页按页记录进度，失败可续。
4. **fail-closed 闸门**：落库前逐日校验 `CLOSE_PRICE` 与可信日线一致（不匹配率超阈值即拒绝该日），并记录 `universe_coverage`、`price_match_rate` 等元数据。
5. **不得**用东财 `SECURITY_NAME_ABBR` 推导历史 ST 状态（已证伪），`special_treatment_status` 保持现状，缺口继续挂账。

### 方案 B：只补估值历史（P2-5）

只拉 PE/PB/市值，不派生 universe/停牌/退市。范围小、见效快，但幸存者偏差与停牌缺口继续挂账。

### 方案 C：ST / 行业时点历史

**已证伪**，本轮不做（数据源不具备时点证据）。仍列为硬缺口。

---

## 四、待评审要点

1. 分级是否确为 T2？评审范围是否正确？
2. 方案 A 的「一次采集派生三缺口」是否存在金融正确性风险（尤其：把停牌/退市判定写进 `security_status_event` 的口径是否可辩护）？
3. 961 万行落温层 Parquet 而非控制面 DuckDB，是否符合分层架构与既有事实源约定？
4. `raw` 层页级证据 vs 明细分层，血缘是否足够可审计、可复现？
5. 本轮是否应同时触发 2 年回测复跑以验证估值因子 IC？
6. 明确不做的项（ST 历史、行业时点历史、指数成分历史序列）是否挂账得当？
