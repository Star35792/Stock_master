# 2026-10-21 数据库拆分第二阶段：控制面瘦身 + WarmLayerStore 统一读路径

## 背景

第一阶段已经把 `market.daily_bar`、`raw.daily_bar`、`market.adjustment_factor`、`factor.factor_value` 四张大表按 `trade_year/trade_month` 导出到 `data/warm/exported/`。第二阶段的目标是把 `stock_master.duckdb` 缩小到“控制面 + 热窗口”，同时让 `daily_loop`、因子计算、回测等读写路径在瘦身后的库上仍然可用。

## 本阶段完成

### 1. 控制面 DuckDB 瘦身

执行脚本 `backend/scripts/maintenance/slim_metadata_db.py`：

- 备份：`backend/data/backups/stock_master-pre-slim-*.duckdb`
- 热窗口：保留最近 90 天（`trade_date >= 2026-07-23`）
- 结果：
  - 原库 4,397 MiB -> 535 MiB（-87.8%）
  - 4 张大表只剩热窗口，历史数据由 Parquet 温层承接
- 瘦身过程复制：
  - 所有控制面小表（`market.security`、`system.job_log`、`factor.factor_definition` 等）
  - 序列（`duckdb_sequences()`）
  - 列默认值（`information_schema.columns.column_default`）
  - 唯一约束转唯一索引（DuckDB 不支持 `ALTER TABLE ADD UNIQUE`，用 `CREATE UNIQUE INDEX` 作为 `ON CONFLICT` 目标）

### 2. WarmLayerStore 读路径抽象

新增 `backend/app/repositories/warm_layer.py`：

- `WarmLayerStore.connect()` 返回内存 DuckDB 连接。
- 自动 ATTACH 控制面库 `ctrl`。
- 对 4 张大表构建 VIEW：`Parquet 历史 UNION ALL ctrl.<table> 热数据`，使应用 SQL 无需改动即可读到完整时间序列。
- 自动处理 Parquet 分区列（`trade_year`、`trade_month`）与控制面表列不一致的问题。
- 暴露 `market.prices_qfq` 视图，指向 `data/warm/curated/prices_qfq`。

### 3. 写路径修复

瘦身后库缺失部分唯一索引/约束，导致 `daily_loop` 的 upsert 失败。修复：

- `backend/app/models/market/daily_bar.py`：`UniqueConstraint` 从 `(symbol, trade_date)` 改为 `(symbol, trade_date, adjust_type)`，与实际数据语义一致。
- `backend/app/repositories/market/daily_bar.py`：`upsert_from_raw` 的冲突目标改为 `(symbol, trade_date, adjust_type)`。
- `backend/scripts/maintenance/fix_slimmed_constraints.py`：对 `UNIQUE` 约束改用 `CREATE UNIQUE INDEX`，不再使用不支持的 `ALTER TABLE ADD UNIQUE`。
- 运行修复脚本为当前瘦身后库补齐 `factor.factor_profile` 等缺失的唯一索引。

`AdjustmentBuildService.rebuild_qfq` 由 `DELETE + INSERT` 改为 `INSERT ... ON CONFLICT DO UPDATE`：

- DuckDB 在同一事务内 DELETE 的行对后续 INSERT 不可见，导致重复键错误；upsert 规避该行为。
- 同时移除 `_DELETE_FACTORS_SQL`。

### 4. daily_loop 认证门调整

`backend/app/scheduler/daily_loop.py` 的 `_gather_certification_metadata`：

- QFQ 覆盖检查不再基于 `market.daily_bar(adjust_type='qfq')`（qfq 行已去物化）。
- 改为检查 `market.adjustment_factor(adjust_type='qfq', source_name='hithink_finance_dump', version=code_version)` 是否覆盖每个 `market.daily_bar(none)` 行。

### 5. 配置开关

`backend/app/config/settings.py`：

- `factor_reads_curated` 默认值保持 `True`。
- `warm_layer_root` 保持 `data/warm`。

### 6. 验证结果

- `daily_loop` 在瘦身后库 + Parquet 温层上跑通：
  - sync trusted = 2
  - adjustment_factor rows = 325,774（source_name='hithink_finance_dump', version=code_version）
  - factor pipeline rows = 21
  - failed = False
  - market & curated release 激活成功
- 低价超跌反弹回测跑通：
  - `python research/backtests/low_price_rebound_backtest.py --start 2024-10-14 --end 2026-10-14 --rebalance-days 20 --top-n 20 --rank-mode intent --out-dir outputs/backtest`
  - 交易日 486 个，信号日 24 个，total_return = 7.83%

## 当前架构

```text
外部数据源
  -> raw landing (本地 FS)
  -> 控制面 DuckDB (stock_master.duckdb, ~535 MiB)
       - 热窗口：最近 90 天的大表数据
       - 控制面：证券、日历、任务日志、因子定义/运行、策略/组合/交易元数据
  -> Parquet 温层 (data/warm/)
       - exported/<tag>/ : 4 张大表历史分区
       - curated/prices_qfq : 前复权 OHLCV
  -> WarmLayerStore (内存 DuckDB VIEW)
       - 合并 Parquet 历史 + ctrl 热窗口
       - 供 research 回测、因子计算读取完整时间序列
```

## 已知问题 / 后续优化

1. **重复索引清理**：修复脚本曾为已存在索引的表额外创建 `ix_slim_*` 索引，已手工清理。后续应让 `slim_metadata_db.py` 在创建前检查是否已有覆盖相同列的唯一索引。
2. ~~**corporate_action source_name**~~（已修复）：`daily_loop` 调用 `AdjustmentBuildService.rebuild_qfq` 时统一传入 `source_name='hithink_finance_dump'`，`build_prices_qfq_from_db` 也使用 `factor_version=code_version`，使 adjustment_factor 与 corporate_action 的 source_name 一致，qfq factor 现在能反映真实除权除息事件（单只股票出现 854 个不同 factor 值）。
3. **热窗口大小**：默认 90 天，可通过 `--retention-days` 调整。若回测需要更长的近期窗口，需相应增大 retention。
4. **自动化调度**：`daily_loop` 已可手动运行；定时自动化可通过 `app.scheduler.daily_loop.create_daily_loop_scheduler()` 或系统 cron/WorkBuddy automation 配置。

## 相关文件

- `backend/app/repositories/warm_layer.py`
- `backend/scripts/maintenance/slim_metadata_db.py`
- `backend/scripts/maintenance/fix_slimmed_constraints.py`
- `backend/app/services/adjustment_build.py`
- `backend/app/scheduler/daily_loop.py`
- `backend/app/models/market/daily_bar.py`
- `backend/app/repositories/market/daily_bar.py`
- `backend/app/config/settings.py`
