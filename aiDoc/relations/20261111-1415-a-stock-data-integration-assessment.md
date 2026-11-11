# a-stock-data 接入评估：数据补充与可靠性提升

- 时间：2026-11-11
- 范围：`a-stock-data-main/`（外部 Skill 项目）→ Stock Master 的接入可行性评估
- 结论性质：**评估报告 + 可复现脚本**，尚未接入生产链路，未改任何生产数据

---

## 0. 一句话结论

`a-stock-data` 能补上本项目当前最痛的两个缺口（**估值快照断层**、**多源对账休眠**），
并且在实测中对拍出了本项目自身的一个既有缺陷（`market.adjustment_factor` 旧版本残留）。

但它**不是 pip 包**，而是一份「结构化 Markdown + 内嵌 Python」，直接 import 会
在 import 时联网取数——必须先做 AST 净化再封装成适配器。

---

## 1. 这个外部项目是什么

`a-stock-data-main/SKILL.md`（442 KB / 7702 行 / 91 个 Python 代码块）：
十五层数据架构、87 个端点、34 个数据源，覆盖行情、研报、资金面、筹码、新闻、
财务、公告、打板、期权、舆情、宏观利率、指数日历、期货、事件驱动、可转债。

| 事实 | 值 |
| --- | --- |
| 代码块数 | 91 |
| 加载后可调用端点 | 109 个公开函数 |
| 零鉴权 | 除 iwencai 语义搜索外全部无需 key |
| 许可 | Apache-2.0（作者 Simon Lin，github.com/simonlin1212/a-stock-data） |

**形态判断**：它是给 AI 编程助手用的 Skill，不是库。集成方式有两条路：

1. **Skill 路线**——装成 WorkBuddy Skill，人工/AI 临时取数用，不进 `raw → market` 流水线；
2. **适配器路线**——把端点代码抽出来，写成 `backend/app/data_sources/` 下的适配器，
   走项目既有的 `raw → 质量闸门 → market` 流程。

本报告按路线 2 评估，因为项目的数据中心规则明确要求「外部数据源必须先进 raw」。

---

## 2. 接入形态：必须做 AST 净化

### 2.1 问题

SKILL.md 里相当一部分代码块在**模块级**直接联网并打印：

```python
quote = tencent_quote(["600519", "000001"])   # 模块级就发请求
print(quote)
```

直接 exec 整个块的后果（实测）：import 即发几十个外网请求、stdout 被灌满、
加载耗时数十秒，且**可能触发东财风控**（`em_get()` 有 5 次/秒、200 次/分钟的限流）。

### 2.2 解法

已实现 `libs/a_stock_data/loader.py`：AST 白名单净化。

保留：`Import` / `FunctionDef` / `ClassDef`；
`Assign` 仅当右侧是字面量或**安全构造**（`re.compile()`、`requests.Session()`）时保留；
丢弃：模块级 `Expr`（打印与 demo 调用）、`If`/`For`/`While`/`With`/`Try` 控制流。

`mootdx`（§1.7，2026-09 起失效留档）改软依赖。

**实测结果**：加载 0.98 秒，剥离 262 条副作用语句，7 个块跳过（可选依赖缺失），
import 零网络请求。

> 注意：`requests.Session()` 只建连接池不发包，属安全；但**任何业务取数调用**
> （`tencent_quote(...)`、`tdx_daily_package(...)`）在模块级都必须剥离。

---

## 3. 本项目的现状缺口（接入前基线）

实测 `backend/data/stock_master.duckdb`（2026-11-11）：

| 项目 | 实测值 | 问题 |
| --- | --- | --- |
| `market.daily_bar` | 413,120 行 / 6,158 标的 / 2026-07-28 ~ 2026-11-11 | 日线本身不缺 |
| `raw.daily_bar` 的源 | `hithink_finance`、`hithink_finance_dump`、`hithink_finance_index` | **三个源同属同花顺，独立源实为 0** |
| `market.daily_bar_source_compare` | **0 行** | 多源对账表建好了但从未写入 |
| `metadata.data_source` | **0 行** | 数据源注册表空着，来源优先级只硬编码在调用点 |
| `market.valuation_snapshot` | 27,817 行，仅 **4 个交易日**（09-18 / 09-23 / 09-24 / 10-28） | **9-24 → 10-28 整月断层**，估值因子与选股直接受影响 |
| `market.security_industry_history` | 5,579 行，每个标的**恰好 1 条**，`effective_to` 全为 NULL | 行业是「当前值」，**无时点历史 → 前视偏差** |
| `market.index_constituent_snapshot` | 120,343 行 / 849 指数，但 `snapshot_date` 全是 2026-09-18 | 单日快照 |
| `market.alternative_data_snapshot` | 13 个 dataset，最新都停在 2026-09-18 | 宏观/情绪族已断供 |

**根因**：多源对账能力早已实现（`daily_bar_market_builder._reconcile_sources:195`），
但 `services/daily_sync.py:69` 传了 `source_name=collection_result.source_name`，
把候选**限定到单源**，于是 `_reconcile_sources` 永远只看到 1 行而跳过。
这是「休眠」而非「缺失」——补一个独立源即可激活。

---

## 4. 活体验证结果（2026-11-10 / 11-11 实跑）

### 4.1 可用端点

| 端点 | 源 | 耗时 | 结果 |
| --- | --- | --- | --- |
| `tencent_quote` | 腾讯 | 0.2s | ✅ PE/PB/市值/换手/涨跌停，含 `is_stale` 僵尸报价标志 |
| `tdx_daily_package` | 通达信官网 | 9.3s | ✅ 全市场 52,605 行日线（含成交额），沪深北 |
| `sina_adjust_factor` | 新浪 | 0.3s | ✅ qfq/hfq 阶梯因子序列 |
| `trading_calendar` | 深交所 | 0.2s | ✅ 官方整月日历，逐日 `is_open` |
| `sina_research_reports` | 新浪 | — | ✅ 研报列表 |
| `ths_eps_forecast` | 同花顺 | — | ✅ 一致预期 EPS（含预测机构数） |
| `sw_industry_history` | 申万 | 0.1s | ❌ 本机 SSL 握手失败（证书链/代理问题，非代码问题） |
| `st_stock_list` | 东财 | 9.7s | ❌ 名单为空或代码重复——端点自带校验，不返回可疑结果 |

> `st_stock_list` 的失败恰好体现了 a-stock-data 的工程取向：**宁可抛错也不返回不可信快照**，
> 与本项目「fail-closed」原则一致。

### 4.2 跨源对拍（核心证据）

脚本：`backend/scripts/ingest/reconcile_with_a_stock_data.py`（可复现）

```
=== 日线对拍 2026-11-10（通达信官网盘后包 vs market.daily_bar）===
  可对拍标的: 6125
  收盘价: 完全一致 5620, 1分以内 6122, 超阈 3
  成交量: 完全一致 5572 (90.97%)
  成交额: <1% 6121 (99.93%)
  个股子集: 5572 只, 成交量完全一致 5572 (100.00%)
```

**结论一：项目日线数据是真的。** 个股 5,572 只成交量 **100% 精确一致**，
收盘价 99.95% 在 1 分以内。

**结论二：553 只成交量差异是口径差，不是数据错。** 逐个核对后确认：
- 549 只精确 100.000×、2 只 10000×；
- 全部 `security_type='index'`；
- 个股（`stock`）**零差异**。

**项目对指数存「股」，通达信存「手」**（比值 = 项目 ÷ 通达信 = 100）。
这是**必须写进对账规则的口径知识**，否则对账会把它误判成冲突、把 553 只正常指数
全部拦进 `SOURCE_CONFLICT`。

> **更正（2026-11-11）**：本节初稿写作「项目存手、通达信存股」，方向写反了。
> 复核依据：`market.daily_bar` 的 `000001.SH` 成交量为 40,486,518,000，
> 通达信 `999999` 为 404,865,177 —— 前者是后者的 100 倍，且 404 亿股与 A 股实际
> 成交量量级相符（若单位是手则意味着 4 万亿股，不成立）。
> 另见 [多源对账激活与独立第二源接入](20261111-1458-multi-source-reconciliation-activation.md)。

**结论三：3 只收盘价差异是非个股。** `000170.SH 50AH优选`、`000867.SH 港中小企`、
`000869.SH HK银行` —— LOF/ETF 类，非个股错误。后续实测确认这三只的成交量比值是
105.4 / 108.7 / 112.4 倍（**基金份额折算**，非整数倍，无法用固定常数归一），
成交额差 1.6% / 8.1% / 13.6% 同源。

### 4.3 复权因子对拍（方向归一后）

```
   symbol  compared_days  exact  max_abs_diff
601799.SH             71     71  3.706724e-11
600519.SH             71     71  0.000000e+00
000001.SZ             71     71  4.319922e-11
000858.SZ             71     71  0.000000e+00
300750.SZ             71     62  2.352687e-05   ← 除权日边界效应
601398.SH             71     71  1.107803e-11
```

**关键发现：两源因子互为倒数。** 新浪 qfq 因子是**除数**（前复权价 = 不复权价 ÷ factor），
项目 v1 因子是**乘数**（前复权价 = 不复权价 × factor）。归一方向后 `proj × sina = 1`
（最大绝对差 3.7e-11），**数值完全一致**。

> 这意味着项目的复权因子**不是**错，而是**方向定义与新浪相反**。
> 但它是个**陷阱**：任何新增对账源若沿用新浪口径直接比数值，会得到
> 「600519 完全一致、601799 差 0.6%」这种按标的随机跳变的假冲突——
> 因为差异只在**有除权的标的**上显现。**对账规则必须显式归一方向。**

---

## 5. 顺带查出：项目自身的两个既有缺陷

### 5.1 缺陷一：`market.adjustment_factor` 旧版本残留（生产路径未受污染，但有脏数据）

实测：

| 项 | 值 |
| --- | --- |
| 总行数 / 唯一 `(symbol, trade_date)` | 1,308,946 / 413,120 |
| 同日存在**多个不同因子值**的组合 | **35,495**（影响 943 只标的） |
| 其中纯冗余（同值多份） | 308,743 组 |
| 真正的异值冲突 | **35,495 组** |

按 `source_name`/`version` 拆解后真相清楚：

| source_name | version | 行数 | 说明 |
| --- | --- | --- | --- |
| `hithink_finance_dump` | **v1** | 413,120 | **当前生产版本，唯一键无重复，无异常值** |
| `hithink_finance_dump` | `local-test` | 308,743 | 历史测试残留，含 560 个极端值（min 0.666） |
| `daily_loop` | `local-test` | 308,743 | 历史测试残留 |
| `daily_loop` | `local-20261014` | 278,340 | 历史测试残留 |

**影响判定**：
- `curated` 层按 `source_name='hithink_finance_dump'` + `version='v1'` 过滤
  （`app/lake/curated.py:184`），**生产 qfq 价格未受污染**；
- 但 `local-test` / `local-20261014` 两版共 587,083 行是**脏数据残留**，
  含明显错误的因子值（0.689655、0.714286 等）；
- 表上**没有唯一约束**（`duckdb_constraints()` 对该表无 unique），
  所以同类问题会再次发生；
- 任何不带 version 过滤直接读该表的查询（如 `api/routes/adjustment_factors.py`）
  都会读到重复行。

### 5.2 缺陷二：多源对账与数据源注册表休眠

- `_reconcile_sources` 因 `source_name` 过滤永不触发；
- `market.daily_bar_source_compare` 表结构完备（12 列，含 `is_conflict` /
  `conflict_reason` / 三个 diff 百分比），**0 行**；
- `metadata.data_source` 表存在（`source_name`/`source_type`/`priority`/`is_active`/`config`）
  但**无仓储类、无任何代码读写、0 行**。

补一个独立源，这三者同时可激活。

---

## 6. 接入方案

### 6.1 分层落点

```
a-stock-data-main/SKILL.md          外部只读资产（不入后端依赖图）
        │
        ▼
libs/a_stock_data/loader.py          AST 净化加载器（已交付，0.98s / 零副作用）
        │
        ▼
backend/app/data_sources/astock_*.py 新适配器（类属性 name / source_type）
        │                                官方源优先：通达信官网 / 深交所 / 中证 / 人民银行
        ▼
backend/app/services/astock_collection.py  采集服务 → raw.import_batch + raw 表
        │
        ▼
backend/app/services/daily_bar_market_builder.py  复用既有质量闸门（不要走裸 SQL 旁路）
        │
        ▼
market.daily_bar / market.daily_bar_source_compare
```

### 6.2 分期建议

**P0 — 激活多源对账（不补新数据，先修管道）**

1. 去掉 `services/daily_sync.py:69` 的 `source_name=` 单源过滤，
   改为「同 `(symbol, trade_date, adjust_type)` 多源候选分组」。
2. 写 `metadata.data_source` 注册行，把硬编码的 source_priority 数值化：
   `hithink_finance`(50) / `hithink_finance_dump`(40) / **新增 tdx_official(70)** /
   `csv`(40) / `manual`(10) / 未注册源(90)。
   > **已实现（2026-11-11）**：注册表落在 `app/services/data_source_registry.py`，
   > 额外用 `is_independent` 标记哪些源是**真正独立上游** —— 三个同花顺源共享一个
   > 上游，互相印证没有意义，只有通达信是另一家厂商。优先级取 70 而非 35：通达信
   > 参与对账但不抢占主源，避免把指数成交量「手/股」的口径问题带进可信层。
   > 详见 [多源对账激活与独立第二源接入](20261111-1458-multi-source-reconciliation-activation.md)。
3. 对账口径规则**必须内建**：
   - 指数类 `volume` 手/股 100× 差异不算冲突；
   - 复权因子先归一方向（乘数 vs 除数）再比；
   - 阈值建议：收盘价 1 分、成交量 1%、成交额 1%。
   > **已实现并修正（2026-11-11）**：落地为 `app/services/reconciliation_rules.py`。
   > 实际阈值与建议不同，且**建议值不够**：单看"收盘价 1 分"会误杀 3 只 LOF 基金
   > （绝对差高达 8.32 元、相对差 0.15%），单看相对容差又会漏掉茅台这类
   > **成交额完全一致但价格差 0.53 元**的真实错价。最终判据是**成交额为锚**：
   > 成交额一致 ⇒ 均价一致 ⇒ 价格只允许绝对容差（0.011 元）；成交额因份额折算
   > 不可比时价格改用相对容差（0.5%）。
4. 冲突处置沿用既有逻辑：`_source_conflict_issue` → `SOURCE_CONFLICT` → 该 bar 不进 market。
   > **已验证（2026-11-11）**：实跑 2026-11-10，6,125 组比对中仅 1 个真实冲突
   > （399002.SZ「深成指R」成交额差 50.4%），误判 0 个。

**P1 — 补估值断层**

`market.valuation_snapshot` 只有 4 个交易日，是当前最硬的缺口（价值因子与
`value_investing_margin` 选股都依赖它）。腾讯 `tencent_quote` 单次可拿全市场
PE/PB/市值/换手率，0.2s 一批，可日频补齐。

注意：腾讯是**当前值**（盘中快照），要进 `market` 必须过
`INTRADAY_SNAPSHOT` 闸门；收盘后（≥15:00）才可作日频估值快照。
且 `pe_ttm` 口径需与东财口径对齐——实测腾讯 600519 PE_TTM 21.35 vs 项目
2026-10-28 快照 PB 6.62 vs 腾讯 PB 6.47，**PB 已有 2% 级别差异**，须先对账再入库。

**P2 — 补宏观/事件族**

`market.alternative_data_snapshot` 已断供（13 个 dataset 停在 2026-09-18），
而因子流水线有 Macro / PolicyEvent / EarningsRevision / CapitalFlow /
Crowding / Sentiment 六个族在消费它。a-stock-data 对应端点：

- 宏观：人民银行社融、国家统计局 PMI、中债收益率曲线、货币网回购定盘利率、LPR
- 事件：东财业绩预告、机构调研、股东增减持、回购、股权质押、新股申购
- 资金：东财融资融券、大宗交易、股东户数、分红、120 日资金流、ETF 份额

**必须遵守项目既有铁律**（2026-11-10 已确立）：
> **外部连接器数据一律不入闸门。** 一致预期 / 评级 / 研报没有首次可得时间，
> 本质是前瞻性预测，接入闸门会让 PIT 约束形同虚设。

因此 `ths_eps_forecast`、研报类数据只能落 `strategy.external_observation`，
进研究卡 `forward_looking` 块，**不进 `market` 可信层**。

**P3 — 行业时点历史（消除前视偏差）**

`market.security_industry_history` 每标的恰好 1 条、`effective_to` 全 NULL，
意味着行业因子在历史上用的是**今天的行业标签** → 前视偏差。
`sw_industry_history()`（申万变迁史）正是解法，但本机 SSL 握手失败，
需先解决证书/代理问题（`pip install -U certifi` 或配置代理）。

### 6.3 接入红线

1. **必须先进 `raw`**，`market` 只由 `daily_bar_market_builder` 写入；
2. **禁止走** `services/hithink_dump_collection.py:246` 的裸 SQL 旁路（绕过质量闸门）；
3. `market.daily_bar` 的 `manual_fixed` 保护边界保持不动（新源不得覆盖人工修复）；
4. `metadata.data_source` 必须登记 `source_name` / `source_type` / `priority` / `config`；
5. 新源数据必须带 `fetched_at` 与 `source_url` 留痕。

---

## 7. 已交付资产

| 文件 | 作用 |
| --- | --- |
| `libs/a_stock_data/loader.py` | AST 净化加载器；`load()` 返回 `(module, skipped)` |
| `libs/a_stock_data/__init__.py` | 包声明 |
| `backend/scripts/ingest/reconcile_with_a_stock_data.py` | 跨源对账脚本（可复现） |

脚本用法：

```bash
cd backend
.venv/Scripts/python.exe -m scripts.ingest.reconcile_with_a_stock_data --trade-date 2026-11-10
```

> **必须用 `-m` 方式运行**：`scripts/ingest/csv/` 目录名遮蔽标准库 `csv`，
> 直接 `python scripts/ingest/xxx.py` 会让 pandas 导入失败
> （`ImportError: cannot import name 'QUOTE_NONNUMERIC' from 'csv'`）。
> 这是一个**已存在的坑**，不只影响本脚本。

---

## 8. 待决策事项

| # | 事项 | 建议 |
| --- | --- | --- |
| 1 | 是否把 `a-stock-data-main/` 长期留在仓库内 | 建议留（Apache-2.0，保留 LICENSE 与来源声明），但**不进后端依赖图**，与 `libs/hithink_sdk` 同构 |
| 2 | 多源对账是否立即上 P0 | 建议上。它不需要新数据就能修，且是所有后续可靠性工作的地基 |
| 3 | 旧版本复权因子残留是否清理 | 建议清理 `local-test` / `local-20261014` 共 587,083 行，并给 `(symbol, trade_date, adjust_type, source_name, version)` 加唯一约束防复发。**属独立改动，需单独评审** |
| 4 | 腾讯估值口径与东财口径不一致 | 上 P1 前必须先对账 PB/PE 口径，不能直接入库 |
| 5 | 申万 SSL 失败 | 需确认是本机 CA 包问题还是网络策略问题，再决定 P3 是否可行 |

---

## 9. 附：为什么这套方案契合项目原则

| 项目原则 | 本方案如何满足 |
| --- | --- |
| 「任何外部数据源默认不可靠」 | 通达信/新浪/腾讯作为**独立第二源**，用于核验而非取代主源 |
| 「多源对账」 | 从休眠状态激活，产出 `market.daily_bar_source_compare` 真实数据 |
| 「原始数据留痕」 | 走 `raw.import_batch` + `raw` 表，带 `fetched_at` / `source_url` |
| 「产出可供策略和回测使用的 market 可信层」 | 估值断层补齐后，价值因子与选股链恢复 |
| 「不把项目实现为依赖单一 AKShare/Tushare 的数据脚本」 | 引入腾讯/通达信/新浪/深交所/中证/人民银行等官方与多厂商源 |
| PIT 铁律（外部前瞻数据不入闸门） | 一致预期/研报单独落 `strategy.external_observation` |
