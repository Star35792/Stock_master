---
doc_type: decision
status: partially_superseded
as_of: 2026-10-21
superseded_by:
  - relations/20261109-1648-control-plane-compaction-and-index-recovery.md
note: >-
  决策在位，文中的容量与行数数字是当日执行快照。
---
# 2026-10-21 数据库拆分第三阶段：扩展温层表 + 修复索引复制

## 背景

第二阶段完成后，`stock_master.duckdb` 中仍有 922 MiB，其中 `raw.corporate_action`、`fundamental.financial_statement`、`raw.financial_payload` 三张表保留完整历史，占用了大量控制面空间。第三阶段目标是把它们也纳入 Parquet 温层，进一步拆分/瘦身单一 DuckDB 文件，同时修复 `slim_metadata_db.py` 在 attached 数据库环境下误把源库索引当作目标库索引的 bug。

## 本阶段完成

### 1. 扩展温层表

在 `backend/app/repositories/warm_layer.py` 中新增第三批温层表：

- `raw.corporate_action` — 按 `ex_date` 分区
- `fundamental.financial_statement` — 按 `report_date` 分区
- `raw.financial_payload` — 按 `report_date` 分区

同步更新：

- `backend/scripts/maintenance/export_warm_layer.py` 的 `EXPORT_TABLES`
- `backend/scripts/maintenance/slim_metadata_db.py` 的 `HOT_WINDOW_TABLES`

`fundamental.financial_indicator` 未纳入，因为 `report` 列为 `YYYY-Q` 字符串，分区策略需要单独设计，且其数据量较小（Parquet 仅 ~3.8 MiB），收益有限。

### 2. WarmLayerStore 支持非 `trade_date` 合并键

`WarmLayerStore.connect()` 之前硬编码使用 `trade_date` 作为 Parquet 与控制面热窗口的 cutoff。新增 `WARM_TABLE_DATE_COLUMNS` 映射，使每张温层表可以使用自己的日期列：

| 表 | cutoff 列 |
|---|---|
| `market.daily_bar` | `trade_date` |
| `raw.daily_bar` | `trade_date` |
| `market.adjustment_factor` | `trade_date` |
| `factor.factor_value` | `trade_date` |
| `raw.corporate_action` | `ex_date` |
| `fundamental.financial_statement` | `report_date` |
| `raw.financial_payload` | `report_date` |

Parquet 分区列仍统一为 `trade_year`/`trade_month`（由日期列派生），保持读取路径一致。

### 3. 修复索引复制 bug

`slim_metadata_db.py` 的 `_copy_unique_indexes` 原来通过 `duckdb_indexes()` 查询目标库已有索引，但目标库 ATTACH 了源库后，`duckdb_indexes()` 会同时返回源库索引，导致所有唯一索引被误判为“已存在”而跳过创建。

修复：在查询目标库已有索引时增加 `database_name = current_database()` 过滤，只比较当前连接的数据库。

同时改进 `_copy_unique_indexes`：

- 从源库 `duckdb_indexes()` 直接复制 `CREATE UNIQUE INDEX` SQL，不再仅依赖 `duckdb_constraints()` 的 UNIQUE 约束（DuckDB 中很多唯一性实际是用 `CREATE UNIQUE INDEX` 实现的）。
- 保留对独立 UNIQUE 约束创建等效索引的兜底逻辑。

### 4. 修复 `build_market_daily` 的冲突目标

`backend/app/services/hithink_dump_collection.py` 的 `build_market_daily` 使用 `ON CONFLICT (symbol, trade_date)`，而 `market.daily_bar` 模型定义为 `UniqueConstraint("symbol", "trade_date", "adjust_type")`。在内存数据库测试（`initialize_database` 通过 SQLAlchemy `create_all`）中会缺失对应的唯一索引，导致 upsert 失败。

修复：把冲突目标改回 `(symbol, trade_date, adjust_type)`，与模型一致。该函数只写入 `adjust_type='none'`，语义不变。

## 执行结果

### 导出

```text
release tag: 20261021-1407
raw.corporate_action:           455,914 rows, 1991-02-26 ~ 2026-10-26
fundamental.financial_statement: 133,773 rows, 2022-09-15 ~ 2026-09-17
raw.financial_payload:          155,794 rows, 2022-09-15 ~ 2026-09-17
```

### 瘦身

```text
原库：922 MiB
新库：665 MiB
减少：257 MiB（27.9%）

raw.corporate_action:           455,914 -> 2,136 rows (ex_date >= 2026-07-23)
fundamental.financial_statement: 133,773 -> 33,807 rows (report_date >= 2026-07-23)
raw.financial_payload:          155,794 -> 33,807 rows (report_date >= 2026-07-23)
```

> 注：665 MiB 大于之前无索引时的 447 MiB，因为修复索引复制 bug 后，所有唯一索引被正确重建，索引本身占用了显著空间。

### WarmLayerStore 合并视图行数校验

```text
raw.corporate_action:           455,914
fundamental.financial_statement: 133,773
raw.financial_payload:          155,794
market.daily_bar:               325,774
raw.daily_bar:                  399,605
market.adjustment_factor:     1,272,799
factor.factor_value:          1,041,533
```

合并后行数与原库完整历史一致。

### 测试

- `ruff check`：通过
- `pytest tests/ --ignore=tests/test_ingest_hithink.py`：**653 passed**
- 之前因索引缺失失败的 `tests/test_hithink_dump_market_recovery.py`：6 passed

## 当前架构

```text
外部数据源
  -> raw landing (本地 FS)
  -> 控制面 DuckDB (stock_master.duckdb, ~665 MiB)
       - 热窗口：最近 90 天的大表数据
       - 控制面：证券、日历、任务日志、因子定义/运行、策略/组合/交易元数据
  -> Parquet 温层 (data/warm/)
       - exported/<tag>/ : 7 张大表历史分区
           - market.daily_bar, raw.daily_bar, market.adjustment_factor, factor.factor_value
           - raw.corporate_action, fundamental.financial_statement, raw.financial_payload
       - curated/prices_qfq : 前复权 OHLCV
  -> WarmLayerStore (内存 DuckDB VIEW)
       - 合并 Parquet 历史 + ctrl 热窗口
       - 供 research 回测、因子计算读取完整时间序列
```

## 已知问题 / 后续优化

1. **索引空间**：DuckDB 唯一索引占用较大。若希望进一步缩小控制面文件，可考虑：
   - 对纯历史查询表（如 `raw.financial_payload`）在控制面中完全不保留，全部走 Parquet；
   - 或者使用更紧凑的索引策略。
2. **`fundamental.financial_indicator`**：尚未纳入温层，待设计 `YYYY-Q` 字符串分区方案。
3. **备份清理**：本次 slim 又生成了新的 `data/backups/stock_master-pre-slim-*.duckdb`（~922 MiB），确认稳定后可按个人文件安全规则归档或移入回收站。
4. **Sequence 默认 warning**：`_copy_defaults` 在 sequence 复制前尝试设置 default 会报 warning，不影响结果；后续可调整复制顺序消除 noise。

## 相关文件

- `backend/app/repositories/warm_layer.py`
- `backend/scripts/maintenance/export_warm_layer.py`
- `backend/scripts/maintenance/slim_metadata_db.py`
- `backend/app/services/hithink_dump_collection.py`
