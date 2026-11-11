# 历史回填能力实测：端点能回溯一年，快照历史不再是死结

> 日期：2026-11-11
> 关联任务：#11 六族 alternative 数据断供修复（第四阶段：历史回填）
> 状态：已交付并验证（回填服务 + 端点能力矩阵 + 12 个月历史落库）

## 一句话

上一轮 PIT 认证的诚实结论是「真正的卡点是**数据历史长度**，不是认证」。
本轮把这句话变成了可执行的解法：**端点本身多数支持按日期回溯**，
历史可以一次性补齐——不必等调度自己攒两个月。同时推翻了一个错误结论：
**炸板池端点一直都在**，此前记的「未找到端点」是没对着端点清单核对。

## 一、端点能力矩阵（实测，不是推测）

对每个端点逐一试了 `date` / `date_ms` / `trade_date` 三种参数：

| dataset | 端点 | 历史参数 | 回显日期 | 可回溯深度 |
|---|---|---|---|---|
| `hot_stock` | `hot-stock-list-history` | `date`（ISO） | 是 | 1 年 |
| `limit_up_pool` | `limit-up-pool` | `date_ms`（毫秒） | **否** | ~15 个月 |
| `failed_limit_up` | `limit-break-pool` | `date_ms`（毫秒） | **否** | ~15 个月 |
| `limit_down_pool` | `limit-down-pool` | `date_ms`（毫秒） | **否** | ~15 个月 |
| `limit_up_statistics` | `limit-up-statistics` | `date`（ISO） | 是 | 1 年 |
| `dragon_tiger` | `dragon-tiger-list` | `date`（ISO） | 是 | 1 年 |
| `dragon_tiger_hot_money` | `dragon-tiger-list?board_type=hot_money` | `date`（ISO） | 是 | 1 年 |
| `limit_up_ladder` | `limit-up-ladder` | 无（端点自带历史序列） | 是 | 端点自带 30 天 |
| `skyrocket` | `skyrocket-list` | **无**（参数被忽略） | 否 | 不可回溯 |
| `anomaly` | `anomaly-analysis-list` | **无**（参数被忽略） | 否 | 不可回溯 |
| `eastmoney_main_fund_rank` | push2 `clist` 实时接口 | **无** | 否 | 不可回溯 |

**公共安全窗口 = 12 个月**（2025-11-12 ~ 2026-11-10）。池类端点虽然能到
15 个月，但因子计算看的是**窗口交集**，多出来的部分没有用。

## 二、两个必须记住的坑

### 坑 1：参数名用错会被**静默忽略**，返回当期数据而不报错

实测 `limit-break-pool`：

| 传入参数 | 返回 | 是否是请求的那天 |
|---|---|---|
| `date_ms=1792425600000` | 29 条 | ✅ 2026-10-20 |
| `date=2026-10-20` | 19 条 | ❌ 当期 |
| `trade_date=2026-10-20` | 19 条 | ❌ 当期 |
| 不传 | 19 条 | ❌ 当期 |

`limit-up-statistics` 正好相反：要 ISO 字符串 `date`，传 `date_ms` 会被忽略。

**端点不会告诉你参数没生效。** 这与上一轮龙虎榜 `board_type` 那一类坑完全
同源——**「返回了数据」不等于「返回的是你要的数据」**。所以每个端点的历史
参数都必须**逐一实测**并记进代码，不能照搬。

### 坑 2：池类端点**不回显请求日期**，不注入就会造成前视污染

`limit-up-pool` / `limit-break-pool` / `limit-down-pool` 的响应里没有日期字段，
`timestamp` 是**响应时刻**——也就是回填当天。

若直接落库，2026-10-20 的涨停数据会被标成 **2026-11-11**。因子取
「`snapshot_date < 目标交易日`」时，这批数据会在 2026-11-11 当天就被读到，
而它描述的是**一个半月前**的市场状态——**教科书级的前视污染**。

修法：调用方**显式注入**请求日期（本轮用 `payload["trade_date"] = ...`），
并在代码里用 `echoes_date` 标志把「回显」与「不���显」两类端点分开，
不靠调用点记得传。

### 附带一条：错误信息会误导

超出池类端点历史窗口时报的是 **`Invalid parameter format`**（参数格式错误），
而榜单类端点报的是 **`date must be within one year`**（超出一年窗口）。
前者会让人以为是毫秒时间戳算错了，实际是**超出历史范围**——照着错误信息
去调格式会白折腾。

## 三、炸板池端点：推翻一个错误结论

上一轮把 `failed_limit_up` 记成「未找到端点」，`limit_up_failure_rate` 与
`overheat_reversal_signal` 两个因子因此留在 `unknown`。

**端点一直都在**：`/api/a-share/special-data/limit-break-pool`。
起因是没对着端点清单核对——同花顺的 API 文档站（`fuyao.aicubes.cn/docs`）
带 sitemap，列出全部 **112 个 MCP 工具端点**，special-data 域下就有
`limit_break_pool` / `limit_down_pool` / `limit_up_statistics` /
`dragon_tiger_department_history` / `abnormal_monitor_list` 等。

> **教训：「找不到端点」是搜索方法的失败，不是端点的不存在。**
> 有文档站 sitemap 这种东西时，先去列清单。

接通后实测：炸板池 19 条落库；`limit-up-statistics` 直接给出
`seal_rate` / `open_stock_count` / `limit_up_stock_count` /
`touched_limit_up_stock_count`，且**交叉验算一致**——2026-10-20 的
`open_stock_count` = 29，与当日炸板池条数 29 完全吻合，
`seal_rate` = 52/81 = 0.6420 = `limit_up_stock_count / touched_limit_up_stock_count`。

两个自洽的独立口径对上了，说明炸板池接对了。

## 四、回填服务（`app/services/hithink_alternative_history.py`）

```bash
python -m scripts.ingest.backfill_alternative_history \
    --start 2025-11-12 --end 2026-11-10 --interval 0.3
```

设计要点：

* **按交易日回溯**，用 `market.trading_calendar` 的 `is_open` 过滤——非交易日
  去请求只会拿回空集，白烧限流额度。
* **每个 dataset 一个抓取器**，参数格式与「是否回显日期」都写在
  `HISTORY_FETCHERS` 表里，一眼能看出哪个端点需要注入日期。
* **不回显日期的端点强制注入请求日期**（见坑 2）。
* **单日失败只记账、继续跑**——一个端点抽风不该让整轮回填白跑；失败进
  `failures` 字典，状态降级为 `failed` 但已完成的部分保留落库。
* **幂等**：落库走 `ON CONFLICT (dataset, snapshot_date, item_key) DO UPDATE`。
* **空集照旧记账**：归一化 0 行进 `empty_datasets`，不伪装成成功（沿用上一轮
  建立的三态语义）。

### 龙虎榜的日期口径

龙虎榜是 **T 日交易、T+1 公布**。回填把 `snapshot_date` 记成**交易日 T**
（与当期采集一致——实测当期采集落的也是 `2026-11-10`，即上一交易日），
由因子的消费规则（`snapshot_date < 目标交易日`）保证最早在 T+1 取用，
那正是公布日，不前视。

若改成记公布日，历史序列反而会与当期采集的序列**对不齐**，制造口径分叉。

## 五、验证

* 新增 `tests/test_alternative_data_history.py` **11 例**：
  请求日期压过响应时刻 / 覆盖全部 dataset / 每个端点用对历史参数 /
  跳过非交易日 / 单日失败不中断整轮 / 幂等 / 未知 dataset 报错 /
  席位留在 item_key / 涨停统计落成一条 market 汇总行 / 抓取器表完整。
* 小范围实跑（2026-10-19 ~ 2026-10-23，5 个交易日 × 7 个 dataset）：
  35 个原始快照 / 1,292 行归一化行 / `status=success`。
  **交叉验算：2026-10-20 炸板池落库 29 条，与独立实测的 29 条完全一致**——
  证明回填拿到的确实是那一天的数据，而不是回填当天的。
* 全量 12 个月回填：2025-11-12 ~ 2026-11-10。

## 六、改动清单

| 文件 | 改动 |
|---|---|
| `app/data_sources/hithink.py` | 新增 `fetch_limit_break_pool` / `fetch_limit_down_pool` / `fetch_limit_up_statistics`；池类分页逻辑抽成 `_fetch_pool_pages` |
| `app/services/hithink_alternative_history.py` | 新增：历史回填服务 + `HISTORY_FETCHERS` 能力表 + `open_trade_dates` |
| `app/services/hithink_alternative_collection.py` | 当期采集接入炸板池 / 跌停池 / 涨停统计 |
| `app/services/alternative_data_supply.py` | `failed_limit_up` 由 `NOT_CONNECTED` 改 `CONNECTED`；新增 `limit_down_pool` / `limit_up_statistics` 登记；修正各端点路径与历史参数说明 |
| `scripts/ingest/backfill_alternative_history.py` | 新增：回填 CLI |
| `tests/test_alternative_data_history.py` | 新增 11 例 |

## 七、遗留

* **`skyrocket` / `anomaly` / `eastmoney_main_fund_rank` 不可回溯**：
  前两个端点忽略日期参数，东财主力资金榜是实时 `clist` 接口。
  → `market_sentiment_score` 与 `sentiment_temperature_percentile` 这两个
  因子仍受限于「只有当期 + 逐日累积」。
* **`limit_up_pool` 等池类端点可回溯 15 个月**，但公共窗口取 12 个月以保证
  因子计算的窗口交集一致。
* **宏观族 7 个因子**仍需确认发布滞后口径（观测期 vs 公布日）。
* **资金流族 5 个因子**需要另一个数据源（东财历史资金流日 K）才能拿到历史。
