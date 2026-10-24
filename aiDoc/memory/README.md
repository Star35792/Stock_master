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
- **研究读路径默认走温层**（2026-10-22）：控制面 `market.daily_bar` 只有约 59 个交易日热窗口，任何跨年回测/IC 都必须读 `data/warm/exported/` 的 Parquet。改回测或研究读路径时，ORM 仓库与 `repositories/research_read.py` 这对孪生实现要同步改。
- 温层连接不要 ATTACH 控制面 DuckDB：DuckDB 禁止同一文件被两个句柄打开（其一为读写）时报 `Unique file handle conflict`。用 `attach_control=False` 建纯 Parquet 连接，控制面读取继续走已打开的 ORM session。
- **DuckDB 跨进程写互斥的工程约定**（2026-10-24）：引擎无服务器模式、文件级写锁不可配置。维护/研究脚本统一走 `app/utils/duckdb_write_lock.py`（锁文件 `data/duckdb_write.lock`，O_EXCL 原子获取、崩溃 stale 抢占、超时报持有者 PID）排队写库，避免跑到一半才报 conflict；FastAPI 进程不持锁（长驻单写者）。更彻底的解法（strategy 研究写域拆独立 duckdb 文件 + 查询面 Parquet 化）是待评审的 T2 方向。
- **P2 偏差源回填的两个近似假设**（2026-10-24，影响一切消费这些表的数字解读）：①涨跌停回填（控制面 97% 覆盖）对历史期 ST 用 name 前缀近似当前期——主板 ST 的 5% 带宽在历史上被高估为 10%（偏宽松方向）；②行业时点表是「当前标签回填」（source=`current_label_backfill_20261024`，5,579 只、effective_from=上市日），假设行业从未变更。真实历史数据接入后用 `record_interval` 追加区间即可，无需重建。
- DuckDB `corr()` 对退化分组返回 **NaN 而不是 NULL**，写进 `DECIMAL` 列会抛 `Conversion Error: Could not cast value nan`。任何 SQL 聚合结果写入定点列之前都要先过滤 NaN/inf。
- 涨跌停价格不能用「涨跌幅≈10%」反推校验：当日收盘涨 10% 不等于收盘价等于涨停价。正确校验是不变式 `high <= limit_up` 且 `low >= limit_down`（剔除上市前 5 个交易日后真实违规率 0.092%）。推导锚点必须是当日自身的 `pre_close`，不能用前一行的 `close`（除权除息日两者不等）。
- 因子方向不能靠直觉设定：本轮实测 7 个价量因子里 `momentum_20`/`momentum_60`/`liquidity_20` 的 RankIC 显著为负（IR 在 -0.26~-0.33），默认预设给它们「越高越好」导致回测亏损。任何因子进策略前先查 `factor.factor_profile` 的 RankIC 符号。
- 日频换仓的 20 股等权组合会把毛收益吃光：本轮 `ic_aligned` 回测毛收益约 +4.6%，换手率 71、佣金 4.5 万（占初始资金 4.5%），净收益只剩 +0.08%。**2026-10-24 已在平台复现缓解**：同一因子组合改 `every_n_trading_days` 20 日调仓后净 +12.36%（佣金降至 6.2 千，夏普 1.42）；叠加与 paper 同源的组合约束后净 +11.72%、回撤更浅（-11.1%）、夏普 1.43。rolling（40/40/40，2 窗）频率效应跨窗一致：20 日调仓 2/2 窗为正（均值 +6.16%），daily 仅 1/2（均值 +1.10%）。结论与限制见 `aiDoc/research/20261024-1205-rebalance-frequency-net-return.md`（含幸存者/红利剔除偏差、训练窗不选参，不可表述为可信策略收益）。`v1-qfq` 温层仅 120 个开市日，扩 rolling 窗数需回填更长历史或切 `v2-qfq-2y`（483 日）。
- **duckdb-engine 的 `:memory:` 是按线程隔离的 SingletonThreadPool**：conftest 里建的内存 DuckDB schema/表只存在于创建它的线程；TestClient 会在 anyio 工作线程处理请求，请求内发真实 DuckDB SQL 会拿到全新的空库并报 `Catalog Error: schema does not exist`。既有 API 测试全靠 monkeypatch 路由 service 才没踩到；新增要在请求线程里真查 DuckDB 的 API 测试时，同样 monkeypatch 仓储或显式使用线程绑定方案（2026-10-23 持仓账本 API 测试踩坑）。
- 真实持仓账本（`positions` 域）自 2026-10-23 起存独立 SQLite `data/positions.db`：这是用户明确的存储决策、唯一 DuckDB 例外（ADR：`aiDoc/relations/20261023-1925-position-ledger-sqlite.md`）。SQLite 金额列用整数 quants（0.0001 元）满足定点规则；账本服务 `add_trade` 需要同时拿 SQLite 会话（写流水）和 DuckDB 会话（查证券主数据），双库参数不要混用。
