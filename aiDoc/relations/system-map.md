# 系统关系 Reference

## 主数据链路

```text
外部来源
  -> raw
  -> market
  -> factor
  -> research/backtest
  -> portfolio/risk
  -> trade
  -> operations / frontend
```

## 事实源边界

- DuckDB 是当前唯一业务事实存储，本地 Parquet 保存 curated 温层数据集。回测与因子研究的**读**路径可以直接读温层 Parquet（`research_read_source=warm`），**写路径始终只走 ORM**；温层连接不 ATTACH 控制面文件（`attach_control=False`），公司行为与交易日历仍从 ORM 读。
- `raw` 保存来源、批次、原始 payload 和异常数据。
- `market` 是可信消费层。
- `factor` 保存可复现的因子定义、运行和值。
- `strategy` 保存回测运行、成交、净值、持仓和绩效。
- `portfolio` 保存不可执行草案，`risk` 保存绑定服务端事实的评估，`trade` 保存短时确认和 approval 唯一绑定的 paper 执行批次。公共执行必须按 `plan -> equity/readiness -> risk evaluation -> approval -> execution batch` 单向消费，不能绕过确认直接调用旧纸盘内核。`trade` 在确认与执行两阶段都按 `market.daily_bar`（`adjust_type=none`）执行 `AShareTradabilityPolicy`；执行批次哈希显式包含 `execution_day_bar_hash`，把批次绑定到预演时的执行日行情快照
- `system` 保存任务、质量问题和审计。

## 工具边界

- `backend/app/lake`：本地 Parquet 数据湖和批量扫描工具；curated 数据集是因子服务可配置的生产消费源，与 DuckDB 事实层共同组成单机存储架构。
- `libs/hithink_sdk/marketdb`：DuckDB SDK/toolkit，适合 notebook 和历史数据分析，不等同于后端 `market` 可信层。
- API 生命周期管理 `AutoDataSyncWorker`：启动后在后台线程串行执行全市场未复权日线同步，每次结束后等待配置间隔再检查，关闭时等待当前同步结束；使用单 worker，任务与水位复用 `JobLog`（物理表 `metadata.job_log`）。
- 同进程 `PriceValueInputPrepWorker` 于交易日 21:00 后准备当晚估值观察值与供应商披露 ROE，不生成通用 ROE 代理；`PriceValueScreenWorker` 在 00:00 生成上一完成交易日的只读全市场报告，并于启动和白天补查。三个 worker 共用进程内数据库写锁；仅适用于单 API worker，其他独立写库进程仍须避开。HiThink 证券与日历刷新先留 raw，证券上市日低覆盖时重取小页，实际变化才写 market，已知上市日冲突即失败。东方财富当前估值接口失败时，同日晚间可使用供应商标注**当天**交易日期的估值分析分页；估值与 ROE 原始页先入 `raw`，同日可信日线及公告时间验收后才附入研究用 `market.valuation_snapshot`。这些行带 `_research_batch_id`，通用估值/市值因子读取会排除；报告仅作人工复核。旧估值直写入口已禁用。
- 手工历史估值补采脚本按供应商 `TRADE_DATE` 获取估值分析日期列表，只向 DuckDB `raw` 留存独立批次和真实抓取时刻。因尚无历史首次可得时间与生产因子消费隔离，它不发布 `market`、`factor` 或筛选报告；运行脚本需与持有 DuckDB 的 API 进程错开。
- 独立调度脚本仍基于 APScheduler blocking scheduler；`daily_loop` 编排器按交易日串联 `daily_sync` 与 `factor_pipeline`，sync 失败时不继续执行因子计算。不要与 API 自动同步同时对同一 DuckDB 文件运行独立写库进程；API 自动同步不触发因子或交易。

## 文档同步触发

- 数据流、事实源、存储边界变化：更新 `aiDoc/relations/trusted-daily-data-center.md`。
- 数据导入和质量规则变化：更新 `aiDoc/data/`。
- 调度、回填、数据湖边界变化：更新本文件，并按需更新深入架构文档。
- 模拟盘确认/执行接入执行日可交易性、`execution_day_bar_hash` 绑定或 `PaperExecutionService` 政策变化：更新 `aiDoc/plans/hard-gates-execution-plan.md` 与 `aiDoc/plans/roadmap.md` 的 V5.3 小节。
- 组合风险预算（`PortfolioRiskBudget`）、约束与事前风控同源或风险预算违反原因记录变化：更新 `aiDoc/plans/hard-gates-execution-plan.md` 第 4 节与 `aiDoc/plans/roadmap.md` 的 V5.1 小节。
