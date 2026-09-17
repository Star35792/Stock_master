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

- DuckDB 是当前唯一业务事实存储，本地 Parquet 保存 curated 温层数据集。
- `raw` 保存来源、批次、原始 payload 和异常数据。
- `market` 是可信消费层。
- `factor` 保存可复现的因子定义、运行和值。
- `strategy` 保存回测运行、成交、净值、持仓和绩效。
- `portfolio` 保存不可执行草案，`risk` 保存绑定服务端事实的评估，`trade` 保存短时确认和 approval 唯一绑定的 paper 执行批次。公共执行必须按 `plan -> equity/readiness -> risk evaluation -> approval -> execution batch` 单向消费，不能绕过确认直接调用旧纸盘内核。
- `system` 保存任务、质量问题和审计。

## 工具边界

- `backend/app/lake`：本地 Parquet 数据湖和批量扫描工具；curated 数据集是因子服务可配置的生产消费源，与 DuckDB 事实层共同组成单机存储架构。
- `libs/hithink_sdk/marketdb`：DuckDB SDK/toolkit，适合 notebook 和历史数据分析，不等同于后端 `market` 可信层。
- 调度脚本基于 APScheduler blocking scheduler，不是 FastAPI 内置后台任务；`daily_loop` 编排器按交易日串联 `daily_sync` 与 `factor_pipeline`，sync 失败时不继续执行因子计算，所有步骤写入 `system.job_log`。

## 文档同步触发

- 数据流、事实源、存储边界变化：更新 `aiDoc/relations/trusted-daily-data-center.md`。
- 数据导入和质量规则变化：更新 `aiDoc/data/`。
- 调度、回填、数据湖边界变化：更新本文件，并按需更新深入架构文档。
