# 分层存储架构

## 1. 决策

Stock Master 不再以 PostgreSQL 承载全部数据。生产存储采用四层职责分离：

```text
外部数据源
  -> raw landing / cold archive (MinIO)
  -> trusted warm datasets (Parquet)
  -> DuckDB query and transformation
  -> Redis hot views
  -> API / factor / backtest / portfolio / risk

PostgreSQL = control plane and transactional truth
MinIO      = cold object storage and durable Parquet backing
Parquet    = warm analytical facts
DuckDB     = warm query / transformation engine, not a standalone server
Redis      = hot derived cache, never the only copy
```

“可信事实源”不再等于某个数据库。可信性由数据契约、不可变版本、manifest、校验状态、血缘和发布指针共同定义。

## 2. 各存储职责

### PostgreSQL：控制面与事务事实

保留小体量、强事务、强约束、需要行级更新或审计的记录：

- `system`：任务日志、数据质量问题、数据源配置、数据集版本目录、发布指针。
- `market` 元数据：证券主数据、交易日历、人工修复指令、来源对账结论。
- `factor` 元数据：因子定义、运行记录、profile/诊断摘要。
- `strategy` 元数据：回测运行合同、参数、状态、绩效摘要；大体量逐日结果迁往温层。
- `portfolio`、`risk`、`trade`：计划、确认、风控、订单、成交、现金、持仓和审计，继续以 PostgreSQL 为事务事实源。

PostgreSQL 不再作为历史行情、逐证券因子值、逐日回测净值/持仓等宽表和长表的主力存储。

### DuckDB + Parquet：温数据主存储

Parquet 保存列式数据文件，DuckDB 负责查询、转换、质量检查和批量计算。主要数据集：

- `raw.daily_bar`、原始财务、原始估值、原始另类数据快照。
- `market.daily_bar`、公司行为、复权因子、财务事实、估值快照、另类数据事实。
- `factor.factor_value`。
- 回测逐日净值、逐日持仓、模拟明细之外的大规模研究中间产物。

默认分区：

| 数据集 | 建议分区 | 排序键 |
| --- | --- | --- |
| 日线/估值/因子 | `trade_year/trade_month` | `trade_date, symbol` |
| 财务事实 | `report_year` | `available_at, symbol, report_period` |
| 公司行为 | `event_year` | `effective_date, symbol` |
| 回测逐日结果 | `run_id` | `trade_date, symbol` |

禁止按单个 `symbol` 生成海量小分区。写入先进入 staging，校验通过后生成不可变 release，再原子切换 active manifest。修订数据生成新 release，不原地修改已发布 Parquet。

### Redis：热数据派生层

Redis 只保存可重建、短生命周期的读取视图：

- 最新行情摘要和最近 K 线窗口。
- 最新因子横截面、榜单和常用筛选结果。
- 运维概览、任务进度、短期查询结果。
- 幂等锁或短时协调状态（交易事实仍在 PostgreSQL）。

缓存键必须包含数据集 release/version；发布新温层版本后通过版本化键自然失效，避免大范围删除。Redis 故障只能导致降级回源，不能导致事实丢失。

### MinIO：冷存储与温层持久化后端

MinIO 保存：

- 外部供应商原始文件和不可变 landing 对象。
- 已发布 Parquet release 的持久副本。
- 历史 manifest、校验报告、回测/研究制品和备份。
- 本地温层淘汰后的历史版本。

对象键包含 `dataset/release/partition`，对象启用 checksum；不得用覆盖同键模拟更新。生命周期规则负责把过旧版本转冷或清理，但 active release 和审计所引用的版本不得删除。

## 3. 数据集发布合同

每个温层 release 至少记录：

- `dataset`、`release`、`schema_version`、`storage_uri`。
- 来源对象及 checksum。
- `row_count`、日期范围、分区信息。
- `created_at`、`code_version`、质量状态。
- `parent_release`（增量或修订时）。
- Parquet 文件清单及每个文件 checksum。

发布顺序固定为：

1. 写 staging 文件。
2. DuckDB 校验 schema、主键唯一性、行数、日期范围和金融质量规则。
3. 上传 MinIO 并核对 checksum。
4. 在 PostgreSQL 事务中登记 release 并切换 active 指针。
5. 预热 Redis 常用视图。
6. 延迟清理旧本地缓存；不能先删旧版本再发布新版本。

## 4. 时点、精度与血缘

- 价格、金额和比率在 Parquet 中使用定点 `DECIMAL`，禁止为了方便统一转为 `DOUBLE`。
- 财务、估值和事件数据保留 `source_available_at` / `first_seen_at` / `effective_at`，因子只能读取计算时点已可得版本。
- 每条因子 release 绑定输入 market release、因子定义版本、代码版本和参数。
- 人工修复写 PostgreSQL 审计记录，并据此重建新的温层 release；不得直接编辑 Parquet 文件。
- 回测与风控必须绑定明确 release，禁止使用无版本的“最新数据”重放历史结果。

## 5. 切换顺序

本次按 clean cut 执行：历史 PostgreSQL 分析数据无需迁移或保留，只保留字段契约、质量规则、血缘语义和事务域表结构。旧环境可直接重新初始化并从外部源重建分析数据。

1. 基础设施：增加 MinIO 配置、对象存储客户端、数据集 catalog、分层策略和健康检查；将现有 `lake` 升级为 warm store 基础。
2. 日线主路径：新的 `raw` landing 进入 MinIO，校验后的 `market.daily_bar` 直接发布 Parquet release；DuckDB 承担查询、因子和回测历史扫描，Redis 承担最近窗口。
3. 因子值：按运行批量写 Parquet，PostgreSQL 只保留定义、运行与摘要；API 从 DuckDB 分页查询。
4. 财务/估值/另类数据和回测明细按相同模式重建。
5. 删除旧 PostgreSQL 大表属于独立 schema cleanup；只在所有代码引用移除后执行，不要求搬运旧行数据。

## 6. 验收标准

- 新环境从空 catalog 重建后，核心 API、因子和回测可以使用新数据 release 工作。
- PostgreSQL 中的交易、风控和审计事务行为不变。
- 相同 release 的 DuckDB 查询结果可重复，重启后不依赖 Redis。
- Redis 清空后 API 可回源并恢复热数据。
- MinIO 暂时不可用时不能发布不完整 release；已有本地 active release 仍可读。
- 任意回测结果可追溯到输入对象 checksum、温层 release、代码和参数版本。

## 7. 明确非目标

- 不把 DuckDB 当多写并发 OLTP 数据库。
- 不把 Redis 当事实源或消息可靠队列。
- 不保留或搬运当前 PostgreSQL 中的历史分析数据；只保留结构与契约并重新采集。
- 不在切换时迁移旧表行数据，也不建设双写兼容层。
- 不在首阶段引入 Spark、Iceberg、Delta Lake 等分布式组件；个人量化场景优先保持 DuckDB + Parquet + MinIO 的低运维复杂度。
