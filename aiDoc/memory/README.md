# 项目记忆 Reference

本文件只记录跨任务稳定有效的事实、偏好和踩坑经验。不要记录一次性过程、临时计划或未验证推测。

## 稳定事实

- 用户希望 vibe coding 时由 AI 主动维护产品、架构、代码规则、计划和记忆文档，不要让用户手动追文档。
- 项目文档采用 `AGENT.MD` 作为 AI 协作规则唯一真源，`aiDoc/` 作为唯一项目文档目录，同时承载正式设计和轻量 reference。
- 当前项目已经进入较大体量阶段，默认应通过 reference 精确读取相关文档，避免每次全量读取。
- 日级数据→因子闭环由 `app/scheduler/daily_loop.py` 编排：`daily_sync` 构建 `market` 后，若 `factor_reads_curated=true` 且 `factor_adjust_type='qfq'`，则从 `market.daily_bar` 和 `raw.corporate_action` 重建 `curated/prices_qfq` 温层，再运行 `factor_pipeline` 滚动重算因子；`system.job_log` 记录每次运行状态，sync 或 curated 重建失败时都不继续执行因子计算。
- **存储约定：不要长期堆放全量数据库副本。** `backend/data/stock_master.duckdb` 是唯一事实库，2026-10-13 已清理 6 个历史全量副本（累计 31.6 GiB，`backend/data` 从 40G 降至 8.3G）。需要灾备时按需临时导出或建立滚动保留策略，不要在仓库目录内保留多个完整 `.duckdb`。诊断与方案见 `aiDoc/relations/20261013-2207-storage-diagnosis-and-plan.md`。
- `market.daily_bar` 的 `qfq` 行是 `none × adjustment_factor` 的确定性产物（1036 万行中 99.99% 可精确复现），属于可派生的存储冗余；但 `app/lake/curated.py` 与 `app/services/adjustment_build.py` 直接读这些行，移除前必须同步改造读取路径。

## 工作偏好

- 文档更新要跟随代码事实，但不要为了小 bug fix 强行改文档。
- 规则要能约束后续 AI 工具：代码改变能力边界、API、schema、架构、计划或长期记忆时，必须同步对应文档。
- 读取文档时先读索引和 reference，再按需读 `aiDoc/` 中的深入专题文档。

## 已知风险

- 文档容易低估代码进度，尤其是 V4 研究入口、V6 工作台、上层代理因子、schema 扩展和数据工具边界。
- 行业/政策类因子目前仍可能依赖当前行业标签；生产评分前需要历史行业映射。
- 财务因子仍需从报告期/报告日期进一步升级到公告日或首次可得时间口径。
- 估值因子点在时：valuation_snapshot 是收盘后市值（close×股本），属于 end-of-day 行情数据，因子查找用 <=（同日可用，策略次日开盘执行无前视）；financial_statement 仍用严格 <（公告次日才用）。两者口径不同是有意为之。
- 估值数据：Eastmoney clist 接口可免 cookie 批量取当前总市值/流通市值/PE/PB（f20/f21/f9/f23），但不支持指定历史日期；东方财富估值分析日期接口可返回指定交易日的 PE_TTM/PB_MRQ 等供应商记录，现仅采集到独立 raw 批次。历史接口不提供可认证的首次可得时间或修订版本，故回填尚不能用于历史全市场收益回测。Xueqiu kline 带历史 pe/pb/market_capital 但需 cookie，当前未配置。
- **2026-10-21 拆分第二阶段完成**：`market.daily_bar`、`raw.daily_bar`、`market.adjustment_factor`、`factor.factor_value` 四张大表已完成控制面瘦身（`stock_master.duckdb` 从 4,397 MiB 降至 535 MiB，-87.8%），历史数据由 `data/warm/exported/` 中的 Parquet 温层承接。新增 `WarmLayerStore` 在内存 DuckDB 中合并 Parquet 历史与控制面热窗口，reader 无需改动 SQL 即可读到完整时间序列。`daily_loop`、因子计算、低价超跌反弹回测均已验证通过。
- `market.daily_bar` 的 `UniqueConstraint` 已修正为 `(symbol, trade_date, adjust_type)`，`AdjustmentBuildService.rebuild_qfq` 已改为 `INSERT ... ON CONFLICT DO UPDATE` 以规避 DuckDB 同事务 DELETE 不可见问题。
