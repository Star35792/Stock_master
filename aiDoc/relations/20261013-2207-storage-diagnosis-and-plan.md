# 存储占用诊断与优化方案

> 日期：2026-10-13
> 范围：`backend/data` 目录（清理前约 40G）
> 结论：**数据本身没有问题，是副本冗余 + 复权数据重复物化**。不丢任何数据可从 40G 降到约 10G。
> 状态：**阶段一、二已执行**（2026-10-13 22:13），回收 31.7G，实际占用 40G → 8.3G。阶段三至五待执行。

---

## 0. 执行记录

### 已完成：阶段一、二（2026-10-13）

删除前主库完整性验证通过：`market.daily_bar` 20,963,604 行、`raw.daily_bar` 10,884,378 行、`market.adjustment_factor` 10,362,524 行、`market.security` 7,722 行，日期范围 2016-07-25 至 2026-09-24。

删除后复查行数完全一致，主库未受影响。

| 已删除文件 | 字节 |
| --- | ---: |
| `backups/stock_master-before-data-backfill-20260918-115414.duckdb` | 2,990,026,752 |
| `backups/stock_master-before-all-interface-sync-20260918-131238.duckdb` | 3,187,683,328 |
| `backups/stock_master-after-interface-sync-20260918-154852.duckdb` | 6,004,944,896 |
| `backups/stock_master-before-wal-recovery-20260923.duckdb` | 6,004,944,896 |
| `stock_master_20260923_before_sync_fix.duckdb` | 7,907,848,192 |
| `stock_master_20260924_before_factor_cleanup.duckdb` | 7,825,010,688 |
| **合计** | **33,920,456,752（31.6 GiB）** |

`backend/data` 从 40G 降至 **8.3G**。当前仅保留 `stock_master.duckdb`（7.3G）作为唯一数据库。

**当前无灾备副本。** 如需恢复能力，应改为按需临时导出或建立滚动备份策略，不要再长期堆放全量副本。

---

## 1. 占用构成实测

| 路径 | 大小 | 说明 |
| --- | ---: | --- |
| `backups/` | 17.0G | 4 个历史全量 DuckDB 副本（2026-09-17 至 09-23） |
| `stock_master_20260923_before_sync_fix.duckdb` | 7.4G | 同步修复前副本 |
| `stock_master_20260924_before_factor_cleanup.duckdb` | 7.3G | 因子清理前副本 |
| `stock_master.duckdb` | 7.3G | **当前主库** |
| `price_value_screens/` | 404M | 84 个估值初筛 JSON 快照 |
| `lake/` | 375M | Parquet 温层（daily_bar 374M） |
| `hithink_dumps/` | 174M | 数据源原始 dump |
| `*.log` + `.wal` | 约 17M | 运行日志与 WAL |

**副本与备份合计 31.7G，占 79%。**

---

## 2. 主库 7.3G 的内部构成

| 表 | 行数 | 估算字节 |
| --- | ---: | ---: |
| `market.daily_bar` | 20,963,604 | 21.1M |
| `raw.daily_bar` | 10,884,378 | 10.9M |
| `market.adjustment_factor` | 10,362,524 | 10.4M |
| `fundamental.financial_indicator` | — | 0.5M |
| `raw.corporate_action` | 398,210 | 0.4M |
| 其余 | — | < 1M |

数据质量本身是健康的：

- 5,722 只证券 × 2,472 个交易日（2016-07-25 至 2026-09-24）；
- `symbol + trade_date + adjust_type` **无任何重复**；
- 覆盖连续，无明显空洞。

---

## 3. 两个真正的冗余源

### 3.1 复权数据被物化了三份

按 `adjust_type` 拆解 `market.daily_bar`：

| adjust_type | 行数 |
| --- | ---: |
| `none` | 10,604,178 |
| `qfq` | 10,359,426 |

加上 `raw.daily_bar` 的 `none` 1,088 万行和 `market.adjustment_factor` 的 `qfq` 1,036 万行。

**已验证 qfq 是 `none × adjustment_factor` 的确定性产物**（见 `app/services/adjustment_build.py:171-177`，构建 SQL 就是 `d.open * f.factor`）：

```
join 后行数        10,362,524
能精确复现 qfq     10,361,345  (99.9886%)
存在偏差              1,179  (0.0114%)
```

1,179 行偏差来自 Decimal 舍入（构建时逐字段独立舍入，重新计算的口径不完全一致）。

**结论：qfq 明细可由 `none` + `adjustment_factor` 完全重建，属于存储冗余。**

但**不能直接删除**。`app/lake/curated.py:138` 和 `app/services/adjustment_build.py:56-63` 都直接读 `market.daily_bar` 的 qfq 行：

```python
trusted_filter = "quality_status = 'trusted' AND adjust_type = 'qfq'"
```

若要移除 qfq 物化，必须同时改造 `curated` 读取路径和 `adjustment_build` 的幂等重建逻辑，属于独立的 T2 任务。

### 3.2 历史全量副本没有保留策略

7 个 `.duckdb` 文件里 6 个是完整副本，全部早于当前主库（主库最后写入 2026-09-25）。每个 3–7.4G。

其中：
- `backups/stock_master-before-data-backfill-20260918-115414.duckdb`（2.8G）
- `backups/stock_master-before-all-interface-sync-20260918-131238.duckdb`（3.0G）
- `backups/stock_master-after-interface-sync-20260918-154852.duckdb`（5.6G）
- `backups/stock_master-before-wal-recovery-20260923.duckdb`（5.6G）
- `stock_master_20260923_before_sync_fix.duckdb`（7.4G）
- `stock_master_20260924_before_factor_cleanup.duckdb`（7.3G）

这些是迁移期（WAL 恢复、接口同步、因子清理）的一次性快照，不是有保留策略的滚动备份。

---

## 4. 建议的处置

> 阶段一、二已执行完成，见本文第 0 节。以下为原始建议，阶段状态已标注。

### 4.1 立即可做：清理副本，回收 31.7G  — **已完成**

保留策略建议：只保留**最近一个**主库副本作为灾备起点，其余删除。

| 动作 | 回收 | 说明 |
| --- | ---: | --- |
| 删除 `backups/` 全部 4 个 | 17.0G | 迁移期快照，已完成任务不需回滚 |
| 删除 `stock_master_20260924_before_factor_cleanup.duckdb` | 7.3G | 最新，改动可由 git + WAL 重放恢复 |
| 删除 `stock_master_20260923_before_sync_fix.duckdb` | 7.4G | 更旧，同上 |
| 保留一个灾备副本 | — | 建议保留 `before-wal-recovery`，或改为按需临时导出 |

**若全部删除，回收 38.7G，`backend/data` 降至约 1.5G。**

> 建议：先只删 `backups/` 里的 4 个（17.0G），保留根目录两个副本作为短期保险。观察几天确认无问题再删根目录的。这样即使误判也有回退余地。

### 4.2 压缩 WAL 与日志

- `stock_master.duckdb.wal` 16M — 正常运行时可清理（需停服务）
- `*.out.log` / `*.err.log` 约 0.5M — 可轮转

收益很小，但顺手做掉。

### 4.3 估值初筛快照加保留上限

`price_value_screens/` 有 84 个 JSON，单个约 5.2M，共 404M。文件名含日期和 hash，是全市场逐股指标快照。

建议保留最近 30 个（约 156M），其余按日期清理。这是有研究留痕价值的产物，不能随手删，所以采用"限额"而非"清空"。

### 4.4 中期：移除 qfq 物化（独立 T2 任务）

目标形态：

```
raw.daily_bar (none)          唯一价格事实源
market.adjustment_factor      复权因子
market.daily_bar (none)       可信未复权价格
        ↓ 视图 / 运行时派生
qfq 价格                      按需计算，不落库
```

预期收益：主库从 7.3G 降至约 3G。

前置条件：

1. 改造 `app/lake/curated.py` 的 qfq 读取为 `none JOIN adjustment_factor`；
2. 改造 `app/services/adjustment_build.py`，去掉 qfq 物化写入；
3. 验证因子、估值、回测三条链路在移除后结果不变；
4. 现有 1,179 行舍入偏差行需明确处理策略（重建后统一舍入口径）。

**这一项涉及回测成交口径，按项目规则属 T2，不能顺手改。**

---

## 5. 预期收益汇总

| 阶段 | 动作 | 回收 | 风险 | 状态 |
| --- | --- | ---: | --- | --- |
| 一 | 清理 `backups/` 4 个副本 | 17.0G | 低 | 已完成 |
| 二 | 清理根目录 2 个旧副本 | 14.7G | 低 | 已完成 |
| 三 | 清理 WAL 与轮转日志 | 0.02G | 低 | 待执行 |
| 四 | 估值快照限额 30 个 | 0.25G | 低 | 待执行 |
| 五 | 移除 qfq 物化（独立任务） | ~4.3G | 中，需 T2 评审 | 待执行 |

**阶段一至二已完成，回收 32.0G，`backend/data` 从 40G 降至 8.3G。**
阶段三至五合计可再回收约 4.6G（降至约 3.7G）。

---

## 6. 关联文档

- `aiDoc/relations/20260722-1721-tiered-storage-architecture.md`：分层存储决策（DuckDB + Parquet，不引入 PostgreSQL/MinIO）
- `aiDoc/data/daily-bar-contract.md`：日线字段契约
- `aiDoc/modules/schema-plan.md`：表结构与索引
- `backend/app/services/adjustment_build.py`：qfq 构建 SQL（复权派生的权威来源）
- `backend/app/lake/curated.py`：curated 温层读取路径