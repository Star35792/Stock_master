---
doc_type: decision
status: partially_superseded
as_of: 2026-10-21
superseded_by:
  - relations/20261112-1341-local-storage-retention-and-cleanup.md
note: >-
  决策在位，文中的容量与行数数字是当日执行快照。
---
# 2026-10-21 数据库拆分第一阶段：大表导出 Parquet 温层

## 背景

`backend/data/stock_master.duckdb` 在 2026-10-20 清理 qfq 冗余和二级索引后仍有 **4.3 GiB**，其中 4 张分析宽表/长表占了绝大部分：

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `market.daily_bar` | 10,668,757 | 未复权可信日线（已去重 qfq） |
| `raw.daily_bar` | 10,995,458 | 原始 landing 日线 |
| `market.adjustment_factor` | 21,310,211 | 复权因子（含历史多版本） |
| `factor.factor_value` | 1,057,199 | 逐日因子值 |

这 4 张表合计 **4,400 万行**，是 DuckDB 单库体积的主要来源，也是 2026-10-14 以来 DuckDB 单文件锁、读取冲突的根因。

## 本阶段完成

执行脚本 `backend/scripts/maintenance/export_warm_layer.py`，将上述 4 张表按 `trade_year/trade_month` 分区导出到本地 Parquet 温层：

- 输出根目录：`backend/data/warm/exported/20261021-1045/`
- 总输出：**694 MiB**（ZSTD 压缩）
- 4 张表均通过 `COUNT(*)` 校验，源表与 Parquet 行数完全一致
- 每张表附带 `manifest.json`：记录行数、日期范围、分区结构、文件清单、SHA-256
- 汇总文件：`backend/data/warm/exported/20261021-1045/export_summary.json`

| 表 | Parquet 文件数 | 行数 | 日期范围 |
| --- | --- | --- | --- |
| `market/daily_bar` | 367 | 10,668,757 | 2016-07-25 ~ 2026-10-19 |
| `raw/daily_bar` | 329 | 10,995,458 | 2016-07-25 ~ 2026-10-19 |
| `market/adjustment_factor` | 375 | 21,310,211 | 2016-07-25 ~ 2026-10-19 |
| `factor/factor_value` | 5 | 1,057,199 | 2026-05-27 ~ 2026-10-19 |

## 当前影响

- **DuckDB 主库大小未变**：本阶段只做导出 + 校验，没有删除源表数据。
- **应用代码未变**：因子、回测、API 仍读 DuckDB。
- **备份已就位**：`backend/data/backups/stock_master-pre-split-20261021-104142.duckdb`（4.3 GiB），作为拆分回滚点。

## 后续阶段

### Phase 2：读路径迁移（缩小 DuckDB 占用前必须完成）

把 repository / service 层对 `market.daily_bar`、`raw.daily_bar`、`market.adjustment_factor`、`factor.factor_value` 的**只读查询**切换到 Parquet：

1. 在 `app/lake` 增加 `ExportedTableReader`，统一按 `release_tag` 读取 `data/warm/exported/<release>/<schema>/<table>`。
2. 改造 `DailyBarRepository`、`AdjustmentFactorRepository`、`FactorValueRepository` 的读方法，优先读 Parquet；缺失最新日数据时回退到 DuckDB。
3. 在 `app.config.settings` 增加 `warm_exported_release_tag`，默认指向最新 release。
4. 跑核心测试 + 一次完整 `daily_loop` 验证只读路径正确。

### Phase 3：写路径迁移（真正拆分 DuckDB）

读路径稳定后，把上述 4 张表的写入从 DuckDB 迁出：

1. **Append-only 表**（`raw.daily_bar`、`factor.factor_value`）：新数据直接追加到 Parquet staging，经校验后注册新 release。
2. **幂等更新表**（`market.daily_bar`、`market.adjustment_factor`）：保留 DuckDB 中的近期一个滑动窗口（如最近 90 天）用于幂等写入和快速对账；历史数据以 Parquet release 为真源，DuckDB 中只保留元数据/指针。
3. 或者：在 DuckDB 中保留空表结构 + view 指向 Parquet，写入仍走 DuckDB 临时表，再批量导出。

Phase 3 完成后，DuckDB 主库预计可降至 **数百 MiB 级别**，只剩控制面、事务事实和近期窗口。

## 风险与阻塞

- **T2 变更**：Phase 2/3 改变存储事实源、repository 边界和 daily_loop 行为，必须同步 `aiDoc/relations/20260722-1721-tiered-storage-architecture.md` 和 `aiDoc/modules/schema-plan.md`。
- **数据一致性**：写路径迁移期间必须保证 `daily_loop`、回测、API 看到的数据一致；需要 release 激活机制。
- **单写锁未消失**：只要写入仍集中在少量 DuckDB 表，单文件锁问题会减轻但不会根除；完全根除需要把写也分散或引入队列。

## 建议

Phase 1 已经让用户可以**直接用 Parquet 文件读取历史日线、因子、复权因子**，满足“拆成多个文件、更好读取”的诉求。

Phase 2/3 是较大的工程改动，建议作为独立任务排期，不要与日常回测/数据采集混在一起做。
