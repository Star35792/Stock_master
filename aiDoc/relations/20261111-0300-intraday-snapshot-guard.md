---
doc_type: decision
status: current
as_of: 2026-11-11
---
# ADR：日线「采集时点」守卫——盘中快照不得发布为可信收盘

- 日期：2026-11-11
- 状态：已接受并实施
- 决策级别：T2（新增数据契约守卫；不改 schema、不改 API 形状、不改既有状态枚举）

## 触发原因

2026-11-10 的价值投资报告标的与安全边际建立在**盘中价**上：最后一批日线 `hithink_daily_bar_20261110030248` 创建于 **2026-11-10 11:02:48（上海时间，上午盘中）**，状态 `partial`。它写进 `market.daily_bar` 的 11-10 bar 是 11:00 的快照，却被标成 `trusted`。

它是怎么被发现的：东财估值历史补采对 11-10 整日拒收，价格一致率仅 8.2%（459/5,580）。追下去看到**927 只个股的收盘价高于自己当日的最高价**——收盘价不可能高于当日最高价，出现即证明这根 bar 不是完整交易日。

同一天回溯还发现 **2026-11-06 也踩过同一个坑**：那批在 11-06 14:40（盘中）采集，被 11-07 10:40 的补抓覆盖后才恢复正确。也就是说一周内发生了两次，只是 11-06 那次因为第二天恰好重抓过而没有暴露。

## 决策

在 `market.daily_bar` 的唯一写入方 `DailyBarMarketBuildService` 里加一道**采集时点守卫**：

> 若一根 bar 的 `raw.daily_bar.imported_at` **早于其 trade_date 的 15:30（Asia/Shanghai）**，则无论其数值多干净，都不得发布为 `trusted`。

实现要点：

| 项 | 取值 | 理由 |
| --- | --- | --- |
| 判据字段 | `raw.daily_bar.imported_at` | 采集时刻与 build 时刻是两件事；build 可能在几小时后对着陈旧 raw 行运行 |
| 阈值 | `trade_date 15:30 Asia/Shanghai` | 15:00 收盘 + 30 分钟收盘竞价/供应商定稿缓冲 |
| 比较方向 | 与该 bar **自己的交易日** 比，不按当天几点 | 次日 09:15 补抓前一日完整交易时段是合法回填，不应被误判 |
| 无时间戳 | 不降级 | 「查不到采集时间」不是「盘中采集」的证据；宁可保持原状，也不让历史行静默掉出所有读路径 |
| 落库状态 | `suspect` + `is_suspect=true` | 见下节「为什么不新增 `provisional`」 |
| 质量问题 | 逐 bar `INTRADAY_SNAPSHOT` + 每交易日一行 `INTRADAY_SNAPSHOT_BATCH` | 逐 bar 行保留溯源；批次行给出唯一可执行动作（收盘后重抓） |

## 为什么不新增 `provisional` 状态

最初的设计是新增 `quality_status='provisional'`。实测否决：

```
ALTER TABLE market.daily_bar DROP CONSTRAINT ck_daily_bar_quality_status_allowed
→ Not implemented Error: No support for that ALTER TABLE option yet!
```

DuckDB 1.5.4 **不支持**在既存表上增删 CHECK 约束，新增枚举值只能整表 CTAS 重建 —— 而这正是 2026-11-09 事故里丢掉 40 个序列、22 个唯一索引、133 个带默认值列的操作。为一个语义标签付这个代价不划算。

复用 `suspect` 在行为上完全等价：所有可信读路径（`research_read`、`curated`、`market_coverage_on_date`、回测取数）都是**正向白名单** `quality_status in ('trusted','manual_fixed')`，`suspect` 自动被排除；`market_coverage_on_date` 还额外要求 `is_suspect = false`。语义差异（「数值可疑」vs「时段不完整」）由 `issue_type` 承载，可查询、可区分。

## 连带效果（无需额外代码）

- **认证闸门自动拦截**：`market_coverage_on_date` 只数 trusted/manual_fixed，盘中快照日覆盖率会掉到 0 → `gate_market_coverage_incomplete` 触发 → `publish_eligible=false`。
- **`daily_loop` 额外给出根因**：目标交易日被盘中快照命中时，追加 `reason_codes=["INTRADAY_SNAPSHOT"]`，避免运维看到 `MARKET_CROSS_SECTION_INCOMPLETE` 去查供应商故障。
- **自愈**：`create_daily_loop_scheduler` 默认 18:40 运行，`sync_days=14`，收盘后那次运行会用真实收盘覆盖掉 `suspect` 行（`upsert_from_raw` 只保护 `manual_fixed`，suspect→trusted 正常放行）。

## 影响面核查（真实数据）

用与守卫等价的 SQL 回查 `selected_raw_id → raw.imported_at`：

| 交易日 | raw 行数 | 会被标记 | 说明 |
| --- | --- | --- | --- |
| 2026-11-04 | 18,415 | 0 | 14:40 那批是 11-06 的回填，已在各自收盘后 |
| 2026-11-05 | 18,412 | 0 | 同上 |
| 2026-11-06 | 18,418 | **6,148** | 11-06 14:40 采集；已被 11-07 10:40 覆盖 |
| 2026-11-09 | 6,122 | 0 | 11-10 11:02 采集，已在 11-09 收盘后 |
| 2026-11-10 | 12,270 | **6,122** | 11:02 采集；已被 11-11 02:17 重抓覆盖 |

**当前 `market.daily_bar` 中「被选中的 raw 行属于盘中采集」的行数 = 0**，即历史数据无需回溯修复，守卫是纯预防性的。

## 未做的事（以及为什么）

- **不回溯审计全历史**：历史行多为回填（`imported_at` 远晚于 trade_date），已被上表覆盖；且 81 根 `selected_raw_id` 为空的早期行本就无法溯源。
- **不在守卫触发时自动重抓**：`daily_sync` 是数据管线，自动等待收盘会把一次调度变成数小时阻塞。修复责任交给 18:40 的常规闭环，或运维显式重跑（`HithinkDailySyncService.sync(start_date=end_date=目标日)`）。
- **不改全局 `latest_release_tag` 字典序问题**：那是另一条独立缺陷，已在 2026-11-10 的实践中用「显式 tag」绕开。

## 验证

- `tests/test_trading_session.py`：8 个用例覆盖盘中/收盘后/次日回填/naive 时间戳/UTC 时间戳/缺失时间戳/边界 15:29-15:30。
- `tests/test_market_build.py`：盘中采集→`suspect`+40 分+计数+批次问题行；收盘后→`trusted`；次日回填→`trusted`；盘中但价格非法→仍然 `rejected` 且不计入盘中快照数。
- `tests/test_daily_loop.py`：目标交易日命中盘中快照→`gate_status=blocked`、`publish_eligible=false`、`reason_codes` 含 `INTRADAY_SNAPSHOT`。
