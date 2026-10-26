# Stock Master 路线图

Stock Master 的优先级是：先建立可复现、可审计、可回放的量化数据底座，再做因子、组合、回测和持仓管理能力。任何策略收益都不能建立在不可解释的数据误差上。

**项目边界**：本项目**只用于选股研究与持仓管理**。不连接券商，不做实盘自动化交易，不发送任何真实订单。系统产出止于「研究结论 + 选股名单 + 组合持仓方案 + 风险预算 + 持仓推演」，实际下单由人工在外部券商终端完成。

## 路线图原则

- 数据先于策略：策略只能读取 `market` 和 `factor` 可信层，不直接读取 `raw`。
- 可复现先于速度：同步、构建、修复、因子计算和回测都必须可重复运行。
- 质量先于覆盖：每扩展一个数据域，先定义契约、质量规则、缺失检测和审计闭环。
- 架构先分层，接口后稳定：按 `raw -> market -> factor -> research/backtest -> portfolio/risk` 分层演进。
- 选股与持仓管理是终点：系统不追求自动下单，模拟盘只用于持仓推演与风险验证，不代表通向真实账户。

## 当前状态

新增研究策略：[价格与价值匹配策略](../strategy/20260922-1314-price-value-margin-strategy.md)。已交付只读 FCFE 三情景研究及单股加现金的独立假设历史回测，均可在 `/price-value` 使用。回测支持版本化手工假设、收盘信号次日开盘含费复核、无加仓持有、基准价值/论点失效退出及未成交重试，持久化实际成交、每日现金净值、决策日志和输入快照。预测和股本是显式假设，行情是当前可信版本，固定不可交易、不认证历史时点、不声称样本外验证；存在公司行为或不完整可信数据的区间拒绝运行。下一步补可信估值输入与修订血缘、净预期收益、历史 universe 与事件会计，再进行独立样本外验证、组合现金配置和持仓推演；不改变现有金融正确性硬门槛。

同页已增加全市场 PE/PB/披露 ROE 的只读人工初筛：交易日 21:00 后先在 raw 留存供应商分页与批次，校验同日可信未复权收盘价及有效指标覆盖，午夜生成全量证券覆盖和阻断原因，启动与白天定期补查。晚间研究观察与通用因子隔离，不计算单期 ROE 代理；页面阈值只过滤同一披露报告期的展示，不形成交易信号。已有单股假设回测可以展示期末净盈亏；全市场名单因缺历史首次可得时间与修订血缘，不新增未经认证的历史盈利回测。当前实际利润需分别以已存在的研究运行和模拟账户事实为准；没有记录或投入基线时明确不可计算。下一阶段仍需补历史可得估值、财报修订、历史证券池与多股组合验证。

东方财富估值分析日期列表现可手工补采指定历史交易日，仅保存原始批次、完整响应和真实抓取时间。该补采不解锁初筛或回测；下一阶段先建立历史估值可得时间及与通用因子读取的隔离，再评估可信发布。

**研究读路径已接入 Parquet 温层（2026-10-22）**。此前研究/回测读路径只走 ORM，只能看到控制面热窗口（约 59 个交易日），因此不可能产出任何跨年证据。现在 `DailyBacktestService` 与 `StrategyResearchService` 通过 `WarmResearchReadRepository` 读温层 Parquet，`research_read_source=warm|control` 可切换，`research_min_trading_days`（默认 200）与「窗口不得超过 Parquet 导出末日」两道 fail-closed 闸门防止静默读到被截断的 release。温层连接默认 `attach_control=False`（纯 Parquet），公司行为与交易日历仍走 ORM，规避 DuckDB 同一文件被读写句柄重复打开的 `Unique file handle conflict`。详见 `aiDoc/relations/trusted-daily-data-center.md`。

**因子 IC 证据首次具备样本量（2026-10-22）**。`factor.factor_profile` 此前为空表，导致 `FactorPoolClassifierService` 把全部 71 个因子判为 `frozen`（要求 `min_trade_dates=200`）。现通过 `scripts/factors/backfill_warm_factors.py` 在温层上按 qfq 价格回填 7 个价量因子（`v2-qfq-2y`，2024-05-01~2026-04-30，484 个交易日 / 5,651 只证券 / 18,606,449 行），再用 `scripts/factors/compute_warm_factor_ic.py` 计算逐日 IC 与 RankIC 并落库 3,371 行。分级结果首次非全冻结：`volume_stability_20`（RankIC1 +0.0435，IR 0.60，正相关率 72.5%）与 `max_drawdown_20`（+0.0179）进入 `core`；其余 5 个因 IC 显著为负判为 `frozen`。注意 IC 计算**不受**回测的公司行为 reject 闸门约束（qfq 价格已含权息调整），因此这个窗口不剔除分红送转证券，比回测股票池更接近全市场。

**因子方向与实测效应相反的偏差已定位（2026-10-22）**。第一批温层因子选股回测（run_id=12，2025-11-03~2026-04-30，top_n=20，等权满仓，`a_share_cost_v1`）总收益 **-4.63%**、年化 -9.54%、最大回撤 -18.59%、夏普 -0.361、换手率 55.0、佣金 36,498 元。原因是默认因子方向预设里 `momentum_20` / `momentum_60` / `liquidity_20` 设为「越高越好」，而实测 RankIC1 分别为 -0.0505（IR -0.33，正相关率仅 36.0%）、-0.0408、-0.0401，即方向与真实效应相反且并非噪声。按 IC 证据翻转这三个方向后重跑（run_id=13，`--spec-set ic_aligned`）总收益 **+0.079%**、最大回撤 -12.55%、夏普 +0.100，但换手率升到 71.1、佣金 45,067 元——**毛收益约 +4.6% 被交易成本几乎全额吃掉**。因此下一步的正确方向不是继续调因子权重，而是实现非日频换仓（`rebalance_rule` 当前硬编码只支持 `daily`）以降低换手；在实现之前，任何温层因子选股回测的净收益都不应表述为策略收益。

**分层回测与多窗口组合净收益曲线（2026-10-24，2026-10-25 P0 修正）**。因子选股回测入口新增两项研究能力，均经四角色评审（架构/产品/金融/规则）裁决，不改变既有金融正确性硬门槛。其一，`POST /api/v1/strategy-research/factor-selection/backtests` 与 `/rolling-backtests` 的 `quantile_backtest` 可选参数（`split_factor_name/version`、`n_layers` 2–20、`max_assets_per_layer`、`weighting_mode`）会在单次回测/每个滚动测试窗内，按 split 因子在**决策日**的横截面排序把可投资 universe 切成 `n_layers` 个互斥桶；universe 沿用主请求 `allowed_security_types`，主数据缺失或类型不允许的证券不进入分层。各层运行等权多头 long-only 组合（T+1、含费含税），产出每层净值曲线与 top–bottom（最高减最低层）统计收益差；A 股不可做空，多空差只是**统计收益差**而非可交易空仓，结果强制声明（long_short / bias_disclosure / method / position_target_policy）。2026-10-25 修正首版两个 P0：换层时显式向旧成员发零权重退出目标，空桶同样清仓；分层分支不再复用主策略 top-N 的行情子集，而是独立读取全量层证券的可信未复权行情、公司行为与交易日历并做前后双快照校验。新结果以 `position_target_policy=explicit_zero_exit_v2` 和 `parameters.quantile_data_snapshot` 标识；缺少该声明的旧多决策日分层结果不可用于研究结论，主回测与 rolling `combined` 主曲线不受影响。分层计算不落独立 `backtest_run`，曲线与 spread 内嵌主运行 `parameters.quantile_backtest` 的 JSON、随响应返回 `quantile`；默认 `quantile_backtest=None` 时完全不写入，保持漂移可比性。其二，滚动回测新增 `RollingBacktestResult` 包装：每个测试窗仍是独立 `backtest_run`，并把各窗 test 期 equity 按首尾衔接、归一化本金（**不重置**）拼接为一条组合净收益曲线 `combined`（含再聚合的换手/佣金/夏普/回撤），重叠测试窗 fail-closed 拒绝拼接、各窗结果仍独立可用；该曲线是**研究统计量而非连续可交易策略**，训练期被排除、跨窗不结转持仓。纯函数 `partition_universe_by_factor` 与 `stitch_equity_curves` 承载分桶的确定性、互斥覆盖与拼接的本金接续及重叠守卫；分层与拼接均不重算成本（成本已在引擎逐日计入）。设计裁决、金融正确性不变量与修正记录见 `aiDoc/research/20261024-1800-quantile-and-rolling-curve.md`。已知硬缺口仍沿用在位声明：分层与回测均不扣幸存者、ST/停牌、行业时点偏差（`security_status_event` 0 行、`security_industry_history` 0 行、公司行为事件会计未实现），样本仍带选择-幸存者偏差。

**估值历史研究层已落地（2026-10-26）**。此前价值因子没有任何 IC 证据：`factor.factor_profile` 只有 7 个价量因子（`v2-qfq-2y`）的记录，估值族因子从未有过逐日 IC，因此 `FactorPoolClassifierService` 无法给价值族任何分级依据，扩池首跑在 2024-2026 段因「价量族证据强度已上限」而 fail-closed（2/16 存活）。本轮通过东财 `RPT_VALUEANALYSIS_DET` 补采 2024-05-01 起的估值历史序列，发布为**温层 Parquet 研究表** `research.valuation_history`（不在控制面、无 ORM 模型、无 Alembic 迁移，判定标准见 `aiDoc/modules/schema-plan.md` §17），并配套新增 `scripts/factors/compute_value_factor_ic.py` 计算 `value_earnings_yield` / `value_pb_mrq` 两个价值因子的逐日 IC 与 RankIC，落入 `factor.factor_profile`。第三个因子 `value_pe_ttm` **已退役**（方向性错误：`-PE_TTM` 把亏损股映射成大正数，最便宜五分位 100% 是亏损股；退役证据保留在 `v1-valhist` 供审计，修正后的证据发布到 `v2-valhist`）。

采集链路上修掉了三个会静默污染数据的缺陷：（1）**分页排序**——只按 `TRADE_DATE` 排序时，服务端对同一交易日内部的行序不作保证，实测 8 个交易日的分页在第 1、2 页之间重复 571 个键并同量丢行，此前「覆盖率 0.907」其实是分页错乱造成的伪影，改用复合排序 `TRADE_DATE,SECURITY_CODE` + `sortTypes=1,1` 后跨页重复归零、8/8 交易日全部过闸门；（2）**闸门口径**——覆盖率分母原含 569 个指数 bar，而供应商只提供个股，已改为 `security_type='stock'` 口径，并新增重复键闸门（同一 `(symbol, trade_date)` 出现多次即拒收该日）；（3）**raw 证据体积**——早期版本把 1.6 MB/页的完整响应体写进控制面，一次中断的采集就把 `stock_master.duckdb` 从 726 MB 撑到 1 GB，现改为只落页级元数据（请求参数、响应 SHA256、行数、抓取时刻），明细留在 Parquet。

**这些估值历史行不是时点证据**。它们是今天采集的历史数据，全部行 `pit_verified=false`；`PB_MRQ` 按报告期末切换，最多前视 120 天。因此价值因子的 IC 只能作为「估值比率与未来收益关系」的研究证据，**不得表述为可交易证据**。字段级判定见 `aiDoc/data/20261026-valuation-history-backfill.md`。

**因子池分级新增三道闸门（2026-10-26）**。秩 IC 只说明「单调相关」，不说明「极端分位可交易」。分级器现在除 IC 与覆盖率外，还要求：

- **端点闸门**：1 日多空（top−bottom 分位）价差**严格为正**。这是可交易性的直接证据，也是本轮把 `long_short_forward_return_5d / _20d`、`quantile_monotonicity_1d`、`top_quantile_turnover` 一并落库的原因。
- **PIT 闸门（fail-closed）**：`factor_definition.pit_status` 取 `point_in_time` / `look_ahead` / `unknown`（默认）。`look_ahead` 硬冻结；`unknown` 不得进核心池。本轮审计结论：7 个温层价格量因子为 `point_in_time`（纯历史价格量），`value_earnings_yield` 为 `point_in_time`（PE 按披露日切换），`value_pb_mrq` 为 **`look_ahead`**（PB 按报告期末切换）。
- **生命周期闸门**：`factor_definition.status` 取 `active` / `retired`。`retired` 表示定义已被取代或已知有误，证据保留供审计但硬冻结。

启用三道闸门后**核心池从 4 个收敛到 1 个**（`volume_stability_20@v2-qfq-2y`）：`max_drawdown_20` 因端点为负落榜、`value_earnings_yield` 因端点为负且覆盖率 0.6957 低于 0.70 落榜、`value_pb_mrq` 因 PIT 前视硬冻结。同时修掉两个基础缺陷：IC 脚本的 `session.merge` 不幂等（改用 `ON CONFLICT DO UPDATE` 的 upsert，重跑后行数不变）、`coverage_rate` 的分子分母同取因子自身非空集（改为分母取价格宇宙，`value_earnings_yield` 真实覆盖率 0.6957）。通用价值因子另按金融正确性**排除银行/保险/券商等 122 只金融行业个股**（其 PB/PE 口径与工业股不可横截面比较，且天然便宜会主导「最便宜」分位）。完整证据见 `aiDoc/data/20261026-valuation-history-backfill.md` §6.2~§6.5。

**初筛 ST 阻断已解除（2026-10-26）**。`market.daily_bar.special_treatment_status` 与 `market.security.special_treatment_status` 两个字段此前**没有任何数据源写入过**，字段存在却恒为 NULL；由于初筛把「未知」当作「受限」，连续 83 次运行 0 个可复核候选。现由 `app/market/special_treatment.py` 从证券简称派生（`ST` / `*ST` / `S*ST` / `SST` 前缀 → ST；`退` 后缀 → delisting），并经 `scripts/maintenance/backfill_special_treatment.py` 回填控制面（`market.security` 5,584 行个股中 200 只 ST + 1 只退市；`market.daily_bar` 316,087 行已填）。派生限定 `security_type='stock'`，因为证券主表同时持有概念/行业指数，其名称含「ST」字样（如 `ST板块` 是板块指数）但不是个股。

**ST 历史时点与行业时点仍然不可得**，两者继续挂账、不得当作已解决：供应商在所有历史行上返回的是**当前**简称与**当前**行业板块（2018 与 2026 的名称变化数与行业变化数均为 0），把它们倒填到历史会制造前视而不是补齐缺口。这与 `market.security_status_event`、`market.security_industry_history` 仍是 0 行是同一类缺口。

当前处于 V6.1 只读运维工作台收口后、转向 V5 公共操作闭环的阶段。P0 估值数据接入（Eastmoney cookie-free）和因子批量管道基础设施已落地；P0-3 数据→因子日级闭环调度器已交付，`daily_loop` 按交易日串联 `daily_sync` 与 `factor_pipeline` 并写入 `system.job_log`，sync 失败时不再继续因子计算。就研究链路而言，V3 因子工厂已经扩展为较完整的可复用体系，V4 已具备因子选股回测创建、回测结果查询、绩效归因、批量运行、跨运行比较、样本外验证区间和滚动窗口规划等支撑 API；`out_of_sample` 会在 service 层强制训练期与测试期不重叠，并只读取、回测测试区间，合同记录显式测试边界、协议和窗口标识。滚动执行在不可变训练/选参产物及窗口到运行绑定落地后已可用（每个测试窗生成独立 `backtest_run` 并绑定 `strategy.training_artifact`）；叠加本次分层回测与跨窗组合曲线，因子有效性与策略净收益已可在不重算成本的前提下被稳定评估，但仍不能宣称完整 walk-forward 已完成（参数敏感性、champion/challenger 晋级仍待切片）。

已完成：

- V1 可信日线数据中心最小闭环。
- 参考数据同步、真实采集、raw -> market 构建统一入口。
- 多源对账、人工修复、质量问题和查询 API。
- ETF/指数基础行情识别与指数参考数据同步。
- 缺失日线检测接口。
- 复权和公司行为数据底座。
- 交易日历和证券生命周期查询。
- 数据质量评分 2.0。
- 因子元数据和运行框架。
- 基础价量因子计算和命令行运行入口。
- 横截面变换、行业中性化和因子统计报告。
- 财务、估值、行业、情绪、资金流、拥挤度、宏观、政策、盈利预期和复合因子首版。
- 回测、因子选股研究入口、绩效归因、组合构建和下单前风控闭环。
- 模拟盘订单、成交、账户现金和持仓推演闭环。
- 运维概览接口和 Vue3 运维工作台聚合数据质量、任务、因子、回测和模拟盘状态。

代码验证基线（2026-07-22）：后端常规 `pytest` 通过 226 项并按预期跳过 3 项显式 PostgreSQL 测试，Ruff 检查通过，前端 `npm run build` 通过，Alembic 离线升级 SQL 可完整生成至 `20260721_0023`。前端另以本地 Chrome 对桌面端和 390px 移动端完成真实路由、行情搜索、自选股、K 线与成交量、因子选择、模拟账户下拉、paper-only 禁用边界、中文状态和横向溢出验收。此前已在隔离临时 PostgreSQL 数据库中完成全迁移、`0023` downgrade/upgrade 往返、第二笔成交写入后故障的整事务回滚和双会话同键单批次回放验证。上述结果不代表 V6 写侧运维动作已满足阶段门。
默认 `app_code_version` 使用 `backend/app` Python 源码确定性指纹；部署可通过环境变量覆盖，风险评估、确认和执行消费均拒绝代码版本漂移。

仍需注意：

- CSV 路径暂不作为当前阻塞项。
- P0-3 数据→因子日级闭环已交付：`daily_loop` 编排器串联 `daily_sync` 与 `factor_pipeline`，每次运行写入 `system.job_log`；`system.job_log` 从空表变为有实际写入入口，但数据质量问题和数据源配置表仍需后续接入。
- 数据获取困难是系统性问题：例如 HiThink 当前无法获取 `562820.SH` 集成电路ETF嘉实，需要用东方财富等补充源兜底。补充源不能绕过数据中心，必须先进入 `raw`，再经质量校验构建到 `market`。
- V5.1 组合草案已接入风险预算（`PortfolioRiskBudget`）：行业权重上限、风格暴露、跟踪误差与组合波动率四类约束与组合约束同源，由 `PortfolioBuilder._apply_style_and_tracking_risk` 强制执行（先于组约束执行），风格代理因子来自 `market.valuation_snapshot` 与 `factor.factor_value`，组合收益率按单资产收益率加权汇总，超预算时等比例缩放并在 `PortfolioTarget.reasons` 记录 `style_budget` / `tracking_error_budget` / `volatility_budget`；`PortfolioPlanRead.style_risk` 与持久化 `plan.style_risk` 可回读风格暴露明细、跟踪误差、波动率与缺失基准标记。风格代理口径（size=ln 市值、value=-PB、momentum/volatility/liquidity=factor_value z-score）为显式研究假设，非完整风格模型。V5.2 readiness/净值/ADV/正式风险评估和 V5.3 短时确认/幂等模拟执行已形成公共闭环。执行固定 paper-only 和 local single-user，在内存全批预演通过后才单事务写入；确认、账户、订单、成交和执行批次可追溯，PostgreSQL 回滚与同键并发已在隔离库验证。真实成本、异步部分成交和外部券商对账不在本项目范围内，模拟盘只用于持仓推演与风险验证。
- 本项目不连接券商、不做实盘自动化交易。真实账户只读适配器、券商回报对账、外部订单幂等状态机和 kill switch 均不在项目范围内，也不作为后续路线；模拟盘是终态的持仓推演工具，不是通向真实账户的过渡阶段。
- **尚未补齐、本轮明确延期的数据缺口**（不要当作已完成）：`market.security_status_event` 停牌事件表已建表但仍为 **0 行**，停牌只能靠「当日无 bar」推断，与真实停牌无法区分；`market.security_industry_history` 行业时点标签仍为 **0 行**，因此**行业中性化在拿到时点标签前不可信**，本轮所有回测与 IC 均未使用它。这两项与「公司行为事件会计」一起构成下一轮数据补齐的硬缺口。
- **ST 状态只补到了「当前」，历史仍缺**：`market.security.special_treatment_status` 与 `market.daily_bar.special_treatment_status` 已由证券简称派生并回填（200 只 ST + 1 只退市），因此初筛的 `treatment_unknown_or_restricted` 阻断已解除。但这是**当前状态**而非带日期的历史序列——供应商在所有历史行上返回当前简称，历史 ST 区间无法从本地数据恢复，倒填会制造前视。ST 状态的历史时点继续挂账。
- 涨跌停价格不再依赖供应商字段：`limit_up` / `limit_down` 在控制面 `market.daily_bar` 的 325,774 行里全为 NULL，改由 `app/market/price_limit.py` 按板块（主板 ±10%、创业板/科创板 ±20%、北交所 ±30%、B 股 ±10%；主板 ST 降为 ±5%）从当日 `pre_close` 推导。以不变式 `high <= limit_up` 且 `low >= limit_down` 对真实数据校验，剔除上市前 5 个交易日后违规率 0.092%，残差来自 `pre_close` 本身的数据质量离群值。指数（如 `000300.SH`、`000001.SH`）不套用涨跌幅带。
- 新增上层因子多为代理变量：财务因子已按公告日生效（`report_date < trade_date`，运行元数据显式声明公告日语义与消费的报表修订哈希），报告修订版本化（`fundamental.financial_statement_revision`），金融行业证券从通用结构因子中剔除（行业专属指标待专门数据源），行业因子按 `market.security_industry_history` 时点标签生效（无历史时回退当前标签并显式记录）；历史行业分类数据源与行业专属财务指标仍待接入。

## V1 可信日线数据中心

目标：将多来源日线数据导入本地 PostgreSQL，经留痕、标准化、校验、对账和修复后，形成可供回测和策略使用的 `market.daily_bar`。

状态：已基本完成，后续只做小修小补。

交付：

- `raw`、`market`、`system` schema。
- 证券主数据、交易日历、raw 日线、market 日线。
- CSV 和 HiThink 数据源接入。
- 数据质量问题表、来源对账表、人工修复审计表。
- 日线查询、质量问题查询、来源对账查询、人工修复查询 API。
- 一键日线同步服务和命令行入口。

阶段门：

- 全量测试、lint 和 diff 空白检查通过。
- 任一 `market.daily_bar` 记录可追溯到 raw 来源或人工修复记录。
- 质量问题可查询、可定位、可修复。

## V2 行情可信层增强

目标：补齐量化研究最容易踩坑的数据基础：复权、交易状态、ETF、指数、日历和缺失检测。

### V2.1 ETF 和指数基础行情

状态：已完成。

交付：

- ETF、指数 security type 标准化。
- 参考数据同步覆盖 A 股和 A 股指数。
- 日线采集优先使用证券主数据类型。
- 指数代码识别规则。

阶段门：

- ETF、指数可以进入现有 `market.daily_bar` 契约。
- 同一套查询 API 可服务 stock、etf、index。

### V2.2 缺失日线和交易状态检测

状态：已完成最小接口，后续可增强。

交付：

- 基于 `market.security + market.trading_calendar + market.daily_bar` 检测缺失日线。
- 新增 `market.security_status_event` 表，记录每日交易状态（正常、停牌、ST、*ST、退市整理、未上市、已退市）。
- 缺失检测结合 `market.security_status_event`：真实停牌/退市/未上市不生成 `MISSING_BAR`，状态正常但无 bar 才生成。
- 支持 symbol、security_type、exchange、adjust_type 和日期范围过滤。

下一步增强：

- 增加同步后自动巡检，把缺失结果写入 `system.data_quality_issue`。
- 对接 HiThink / 其他来源的停牌状态字段，自动写入 `market.security_status_event`。
- 对指数、ETF、股票分别配置缺失容忍规则。

阶段门：

- 任意交易日同步后可回答：哪些证券缺数据，哪些是停牌，哪些需要补采。

### V2.3 复权因子和除权除息事件

优先级：高。

交付：

- `market.corporate_action`：分红、送转、配股、拆合股等事件。
- `market.adjustment_factor`：按 symbol、trade_date、adjust_type 存储前复权/后复权因子。
- raw 日线保留 `none` 原始价格，复权价格由因子派生或构建到独立视图/表。
- 复权因子来源、版本、更新时间和质量状态。

阶段门：

- 同一证券同一区间的 `none/qfq/hfq` 可稳定重算。
- 因子变化不会静默覆盖，必须可追踪来源和版本。
- 研究信号可显式选择 `none/qfq/hfq`，但回测成交统一使用可信未复权价格；在公司行为事件会计落地前，命中除权除息事件的区间 fail-closed，避免用复权虚拟价格成交或产生未调整持仓。

### V2.4 交易日历和证券生命周期

优先级：高。

交付：

- 更完整的 SH/SZ/BJ/HK 交易日历。
- 证券上市、退市、暂停上市、恢复上市生命周期字段或事件。
- 半日市、特殊休市、临时休市支持。

阶段门：

- 任一 symbol 在任一日期是否“应有行情”可以被确定。
- 缺失检测不会把未上市、已退市、停牌误判为数据缺失。

### V2.5 数据质量评分 2.0

优先级：中。

交付：

- 价格连续性检查：异常涨跌、复权断点、前收盘不一致。
- 成交量/成交额异常检查：极端值、负值、缺值、单位异常。
- 多源置信度规则：按来源、字段、证券类型配置优先级。
- 质量问题状态流转：open、ignored、fixed、reopened。

阶段门：

- `trusted` 不只是“写入成功”，而是通过明确规则。
- suspect 数据默认不进入回测输入。

## V3 因子工厂

目标：将可复用因子计算从 notebook/脚本沉淀为可重跑、可追溯、可版本化的数据产品。当前这部分已经不只是基础骨架，而是一个覆盖多类研究信号的因子工厂。

### V3.1 因子元数据和运行框架

状态：已完成并可稳定复用，后续随新因子族继续扩展。

交付：

- `factor.factor_definition`：名称、频率、输入、窗口、版本、依赖。
- `factor.factor_run`：运行批次、参数、代码版本、输入范围、状态。
- `factor.factor_value`：symbol、trade_date、factor_name、value、version。
- 因子定义、运行批次、因子值和 profile 查询 API。
- 代码内可调用的因子运行登记、写值和收尾服务。
- 运行参数、代码版本、输入窗口、复权方式和适用证券范围的版本化记录。

阶段门：

- 因子结果可以按版本回放。
- 同一输入和同一代码版本重跑结果一致。

### V3.2 基础价量因子

状态：已完成最小可运行版本，并已演化出更完整的基础价量族。

交付：

- `ret_1d`、`volatility_5`、`momentum_5`、`ma_gap_5`、`volume_chg_1d`、`turnover_rate`。
- 默认启用的中短周期因子，如 `momentum_20`、`momentum_60`、`volatility_20`、`downside_volatility_20`、`max_drawdown_20`、`liquidity_20`、`volume_stability_20`。
- 扩展统计与风险型基础因子，如 `turnover_percentile_120`、`abnormal_volume_pressure`、`reversal_5`、`beta_60`、`residual_volatility_60`。
- 股票、ETF、指数适用范围标记。
- 统一因子定义注册、运行批次登记、因子值幂等写入和命令行入口。
- 因子版本包含复权方式，避免 `none/qfq/hfq` 结果互相覆盖。
- 默认只读取 `quality_status=trusted` 且非 suspect 的 `market.daily_bar`。
- 缺失可信行情或必要字段时跳过；窗口不足时不写入因子值。

阶段门：

- 因子只读取可信 `market` 层。已满足。
- 对停牌、缺失、上市初期窗口不足有明确处理。已满足最小规则，待停牌事件表完善后细分真实停牌。

### V3.3 横截面处理和中性化

状态：已完成最小可运行版本，已能支撑横截面研究和初步归因。

交付：

- winsorize、standardize、rank、industry neutralize。
- 支持传入规模因子后执行 industry/size neutralize；当前不使用成交额替代市值。
- 因子覆盖率、缺失率、均值、标准差、分位数、极值数量、IC / Rank IC、1/5/20 日 forward diagnostics 和 top quantile turnover 持久化报告。
- 横截面派生因子运行批次、定义注册、幂等写入、查询 API 和命令行入口。
- 已扩展到财务、行业、资金流、拥挤度、情绪、宏观、政策、估值和复合因子等多个研究信号族的复用层。
- 批量诊断：`FactorBatchProfilerService` 对 `factor.factor_definition` 中全部活跃因子逐个运行截面诊断，单因子失败按因子隔离且不影响同批其他因子，整批运行写入 `system.job_log`（`status=success/partial/failed`）；派生 `__` 变换因子不重复作为源。`POST /api/v1/factors/batch-profiles` 触发批量运行，`GET /api/v1/factors/batch-profiles/summary` 按因子聚合已持久化 IC 证据（跨日期简单平均，缺失 horizon 返回空而非 0），命令行入口 `scripts/factors/run_batch_profiles.py`。
- 批量诊断默认规模因子已统一为 `market_cap@v1`（2026-10-24）；其与行情复权口径独立，默认路径可生成 `industry_size_neutral`，但历史市值覆盖不足时仍不应把结果解释为完整 IC 证据。
- 财务时点正确性（硬门槛四）：财务因子按供应商公告日 `report_date < trade_date` 生效，运行元数据声明公告日语义与 `statement_content_digest`/`statement_revision_binding`；`fundamental.financial_statement_revision` 保留报告修订历史（修正披露/更正公告不静默覆盖）；金融行业证券从通用结构因子 fail-closed 剔除（`app/services/financial_sector.py`）；行业因子按 `market.security_industry_history` 的时点标签生效，无历史时回退当前标签并在元数据显式记录。历史行业数据源与行业专属财务指标仍待接入。

阶段门：

- 任一因子进入策略前必须有覆盖率和稳定性统计。已具备报告底座，并可通过批量诊断一次性为全部活跃因子生成 IC 证据。
- 规模中性化在市值或可信规模因子接入后启用真实生产计算。

## V4 研究、回测和绩效归因

目标：建立可复现的研究闭环，而不是一次性脚本。

### V4.1 回测事件模型

状态：单次回测研究合同闭环已完成；批量运行、跨运行比较、基准比较、样本外验证区间合同和交易日滚动窗口规划与执行 API 已交付。样本外运行只执行测试区间；滚动窗口通过 `POST /api/v1/strategy-research/rolling-backtests` 可执行，每个窗口生成独立 `backtest_run` 并绑定到 `strategy.training_artifact` 的不可变训练产物（含训练期因子快照与 SHA-256）。滚动执行仍由调用方指定公共策略参数；平台内训练选参首切片已交付（2026-10-24）：`selection_policy=training_ic_v1` 可选启用，逐训练窗按预注册 RankIC 门槛（|mean RankIC1|≥0.02、|IR|≥0.30、方向正率≥0.55、观测≥10）从候选池自动选因子与方向（相对先验只维持或拒绝，<60 开市日的窗不允许翻转），选择证据写入 artifact 快照 v2，window_id 追加 `|sel-<policy>-<config_hash8>` 后缀隔离不同配置，测试窗纯样本外执行；默认不启用时行为与历史逐字节一致。政策定义与四角色评审裁决见 `aiDoc/plans/20261024-1522-training-window-selection-plan.md`。

交付：

- 交易日驱动的回测引擎或 Backtrader 适配层。已完成最小事件引擎。
- 信号次日开盘执行，避免使用当日收盘信号成交。
- 订单、成交、持仓、现金、费用、滑点模型。已完成内存模型。
- 回测参数、代码版本、数据版本和运行状态落库。
- 回测引擎版本化为 `daily_event_v2`：信号只在证券所属交易所的下一开市日执行；当前明确识别 `SH/SZ/BJ/HK` 后缀，未知交易所、缺失或空交易日历均 fail-closed。目标日缺少可信未复权开盘行情时整次运行失败，不再顺延到该证券下一条行情。同侧订单、成交和持仓输出按证券代码稳定排序，重复行情或重复信号直接拒绝。
- 运行参数保存实际消费的可信未复权行情、公司行为和执行日历切片 SHA-256；引擎结束后通过独立 SQLAlchemy session 重新读取并比较，变化时记录前后摘要、标记失败且不保存成功结果。摘要是精确消费切片的补充证据，不替代 `lake.release`/checksum；尚未绑定 release 的 ORM 回测继续保留该血缘缺口。
- 研究 API 与回测服务默认使用 `linear_commission_v1`；显式选择 `a_share_cost_v1` 可加入最低佣金、卖出印花税、过户费和交易所规费，全部费率随研究合同记录。费率属于显式研究假设，尚不按历史日期或证券类别解析真实费率；合同保留 `unquantized_decimal_until_persistence_v1` 精度政策，不能把当前结果解释成完整 A 股实盘成本。公共 paper 确认与执行同样采用固定 `a_share_cost_v1` 参数，并通过执行日 `market.daily_bar`（`adjust_type=none`）执行与回测一致的 `AShareTradabilityPolicy` 检查；执行批次哈希包含 `execution_day_bar_hash` 以绑定快照。
- 成交、每日净值和每日持仓快照落库。
- 代码内可调用的统一回测服务，只读取可信 `market.daily_bar`。
- 研究视角下已满足最小“信号 -> 交易 -> 净值 -> 查询 -> 归因”闭环，并具备批量、比较、验证区间和滚动 walk-forward 执行支撑；`strategy.training_artifact` 与 `backtest_run.training_artifact_id` 已把窗口到运行绑定为不可变产物血缘，训练选参产物内容当前为训练期因子快照，未来可扩展为模型权重/参数。

阶段门：

- 回测输入全部来自 `market` 和 `factor`。
- 同一参数、同一数据版本、同一代码版本结果一致。

### V4.2 策略研究模板

状态：已完成因子选股回测模板及研究合同输入，策略名称、调仓规则、持有期、基准元数据和交易成本均显式记录；更多策略模板仍属后续工作。

策略层资金表达已扩展（2026-10-13）：`CrossSectionSelectionStrategy` 支持可配置因子覆盖率阈值、持币策略、分数门槛、分数加权和部分仓位；`PortfolioBuilder` 支持按分数择优截断和单标的额外上限。新参数已接入 `POST /api/v1/strategy-research/factor-selection/backtests` 请求 schema 与研究合同，并纳入跨运行比较键。默认值保持原有行为，历史研究结果可复现。

交付：

- ETF 轮动、股票池选股、多因子打分、指数择时信号模板。
- 因子输入统一来自 `factor.factor_value`，证券类型来自 `market.security`。
- 因子评分信号可直接接入 V4.1 回测服务并持久化结果。
- 已形成 `StockPoolStrategy`、`EtfRotationStrategy`、`MultiFactorStrategy`、`IndexTimingStrategy` 的最小模板族。
- `POST /api/v1/strategy-research/factor-selection/backtests` 已可用多因子权重、证券类型、复权口径、成本和滑点参数创建因子选股回测。
- 已把因子规格、信号、证券范围、调仓频率、持有期、成本、复权、基准元数据和版本固化为当前研究合同；研究复权口径与未复权成交口径分别记录，公司行为处理策略固定为 `reject`；基准收益与超额收益已有只读比较接口，风险预算仍属后续切片。
- 策略模板通过不可变 `StrategyFactorFrame` 接收因子行；每个决策日只向策略暴露 `as_of_date == signal_date` 的当日横截面，拒绝未来日期、重复观测和跨日 decision frame。研究合同保存规范化因子切片摘要与 `factor_run_id` 集合。该守卫只约束进入 `factor` 层后的观测日期；公告首次可得时间、修订版本、历史成分和幸存者偏差仍必须由上游血缘继续治理，不能据此宣称全部时点问题已解决。

阶段门：

- 策略结果能追溯到因子版本、数据版本和代码版本。已满足。
- 收益、回撤、换手、暴露和交易成本敏感性在 V4.3 统一完善。

### V4.3 绩效归因和风险暴露

状态：已完成最小可运行归因闭环，并保留失败运行状态和错误信息；已交付验证区间合同、交易日窗口生成与执行、不可变训练产物绑定、基准/跨运行比较和同合同回归漂移诊断，参数敏感性与训练选参审计仍属后续 V4 工作。

交付：

- 个股收益贡献、期末行业暴露和可配置因子风格暴露。
- 最大回撤、年化波动率、夏普、卡玛、日胜率、日盈亏比。
- 换手率、交易佣金和年化收益率。
- 绩效报告按回测运行幂等落库，并记录分析代码版本。
- 已满足最小归因层和比较查询；样本外运行已具备不重叠守卫、仅测试窗执行及可追溯窗口合同。滚动窗口已可执行并生成独立 `backtest_run`，每个运行通过 `training_artifact_id` 绑定到不可变训练产物；训练产物验证开关当前默认为未验证，窗口绑定已从合同记录升级为 artifact 外键绑定。平台内自动训练选参与参数敏感性分析仍属后续工作。
- `POST /api/v1/backtests/runs/validation-report` 只读聚合调用方指定的可比 OOS/历史 rolling 运行，保留失败或缺绩效窗口，报告收益、回撤、Sharpe、换手、佣金、交易数和基准覆盖；重叠测试窗只标记、不复利，且响应明确声明训练产物未验证。
- 因子 IC/Rank IC 的 1/5/20 日前瞻收益按证券所属交易所的目标开市日精确取价；目标日停牌或可信行情缺失时不跳到下一条 bar，避免不同证券的 horizon 实际跨度不一致。
- 日线到周/月的 Decimal OHLCV/amount 重采样已形成研究层内部纯函数：使用交易日历判断完整周期，默认剔除不完整周期，任一日 volume/amount 缺失时不发布部分合计；当前不落库、不开放公共行情 API。

阶段门：

- 任何策略进入模拟盘前必须通过归因和风险报告。已具备报告底座。

## V4 建议工作计划（量化研究 + 架构视角）

目标：把“能跑的最小回测”升级为“可复现、可比较、可持续迭代”的研究平台。

核心原则：

- 防止未来函数、幸存者偏差和样本泄漏。
- 所有研究输入都必须显式版本化，包括因子版本、数据版本、代码版本和复权方式。
- 财务、行业、政策、另类数据和宏观数据必须记录可得时间；不能用当前行业分类、当前成分或报告期结束日回填历史信号。
- 策略模板只消费 `market` 和 `factor` 可信层，不直接碰 `raw`。
- 一切结果都要可回放、可对比、可审计。

建议工作顺序：

1. 先统一研究契约
   - 固化策略输入、信号输出、调仓频率、持有期、费用模型和基准定义。
   - 让每次研究运行都带上 `strategy_name`、`signal_lag`、`rebalance_rule`、`benchmark`、`code_version`、`data_version`。
2. 再把策略模板产品化
   - 把股票池、ETF 轮动、多因子打分、指数择时整理成独立模板。
   - 为每个模板明确适用 universe、调仓频率、约束、退出规则和默认风控参数。
3. 然后扩展回测服务化
   - 在现有因子选股回测入口和回测查询 API 基础上，补齐更多策略模板入口。
   - 支持批量运行、参数组合运行和跨运行对比。
   - 对回测结果做幂等落库和回放校验。
4. 接着增强绩效归因
   - 增加样本外评估、滚动窗口、分市场状态分析和成本敏感性。
   - 把行业暴露、风格暴露、换手、回撤和收益贡献统一到研究报告。
5. 最后补齐工程化护栏
   - 持续增加回归测试金样本；当前已覆盖逐决策日 as-of、输入深冻结、次一交易日成交、缺执行行情 fail-closed、费用/滑点、顺序置换确定性、重复输入和运行期数据摘要漂移。
   - 增加结果漂移检查和版本对比。
   - 给失败运行、低覆盖因子和异常绩效生成可追踪告警。

建议的近期交付物：

- 更多策略研究入口服务和命令行入口。
- 标准化策略配置对象。
- 回测结果对比 API。
- 样本外绩效分析与基准对比。
- 回测回归测试集。

## V5 组合、风控和模拟盘

目标：把研究信号转换为可解释、可审计的组合操作，并在模拟环境中验证完整交易链路。

当前状态：V5 最小公共 paper 操作闭环已形成。组合草案、账户净值、风险前核对、服务端 ADV、正式风险评估、短时人工确认、一次性消费、模拟订单/成交和执行批次均可通过 API 与 Vue3 工作台完成并回读。前端已切换为左侧路由导航的多页面工作台，行情页提供可信日线搜索、自选股、最新可消费日线 K 线、成交量副图、十字光标与折叠审计明细；因子页提供中文解释、手动选择与参数配置，并在 2026-10-25 修正因子方向静默固定为“高值优先”的金融语义错误：新选因子方向初始为待确认，逐项显式选择高值/低值优先后方可运行，不依据当前 IC 自动推断，结果回显本次提交的方向快照；最近画像同时显示截至日期并声明仅供诊断，画像日期与回测期重叠或更晚时提示信息泄漏风险；因子、方向或表单变化会取消进行中的请求并使旧结果与持仓失效。模拟盘从 `quant_db` 动态读取账户下拉并贯穿后续风控和执行流程。

### V5.1 组合构建

状态：已完成最小可运行内核，并交付首个公共草案闭环。

交付：

- 目标权重、单标的上限、最小交易单位和现金保留。
- 行业预算及股票、ETF、指数等证券类型预算。
- 组预算超限支持两种执行模式：`proportional_scale`（等比缩放，保持原有默认行为）和 `score_ranked_trim`（按资产分数从低到高削减，被削到 0 的标的离场）。择优截断要求每个资产都带 `score`，缺失时拒绝而不是猜测。
- 支持单标的额外上限 `extra_symbol_caps`，用于个别标的比通用上限更严的场景；它与通用上限在构造期合成单一有效上限，请求权重在构造期即被封顶，单调不增的组约束不会破坏该不变量。
- 风险预算四约束：`max_industry_weight`、`max_style_exposure`、`max_tracking_error`、`max_portfolio_volatility` 与 `benchmark`。风格暴露/跟踪误差/组合波动率在组约束之前强制执行，超预算时等比例缩放权重并记录 `style_budget` / `tracking_error_budget` / `volatility_budget`；跟踪误差预算缺失基准符号或基准价格时 fail-closed。
- 风格代理因子：`size`=ln 市值 z-score、`value`=-PB z-score、`momentum`/`volatility`/`liquidity`=factor_value z-score（`DEFAULT_STYLE_FACTOR_MAP`），可通过请求 `style_factor_map` 覆盖；基准成分股不在因子截面时主动暴露回退为组合暴露（显式简化假设）。
- `PortfolioPlanRead.style_risk` 与持久化 `plan.style_risk` 回读风格暴露明细、跟踪误差、组合波动率、缺失风格与 `missing_benchmark` 标记。
- 当前持仓到目标持仓的买卖数量计算。
- 每个目标记录原始权重、约束后权重、最终权重和调整原因。
- 组合计划和逐标的目标结果落库。
- `POST /api/v1/portfolio/plans` 以 `Idempotency-Key` 和规范化请求哈希幂等保存操作者手工模拟快照；`GET /api/v1/portfolio/plans/{plan_id}` 可完整回读约束、现金和逐标的调整原因。
- Vue3 工作台可创建、回读和解释组合草案；后端不可用进入 demo 模式时禁用写入，草案固定展示“未风控、不可执行”。

阶段门：

- 组合构建结果可解释，不允许隐式超配或负现金。手工模拟草案的公共 API 契约和前端展示已交付；账户事实源、风控和模拟执行仍是后续阶段门。

### V5.2 风控规则

状态：已完成服务端权威输入绑定的公共风险评估，并已连接短时人工确认和 paper-only 执行。

交付：

- 单标的权重上限、行业上限、回撤止损、成交额容量约束。
- 风险增加订单和风险降低订单差异化处理；只有卖出且单标的、行业投影权重都下降时才按风险降低订单放行，避免仅凭卖出方向绕过回撤或换手约束。
- 风控内核已吸收价格偏离、单笔金额和周期累计换手三项可选规则，缺少启用规则所需输入时 fail-closed；当前正式 `paper_pretrade_risk/v1` 仍只启用既有四项规则，新增规则需另行版本化政策后才能进入正式评估。
- 缺失成交额容量数据时默认拒绝下单。
- 风控批次、逐订单结果、逐规则结果和拒绝原因落库。
- `GET /api/v1/portfolio/plans/{plan_id}/risk-readiness` 只读核对草案审计、代码版本、非空调仓、候选 paper 账户现金与全量持仓、证券生命周期与当前行业、交易日历，以及 exact-date 的 `none` 可信行情。
- 就绪响应固定 `execution_allowed=false`、`risk_evaluated=false`、`orders_created=false`，并标记 `local_single_user` 与 `read_committed_diagnostic`；它不是执行凭证，不能被后续订单链路直接消费。
- `trade.paper_account_equity_snapshot` 从正式监控起点开始追加保存服务端计算的日终净值、观测高水位和回撤；禁止用当前持仓回填历史，缺行情、未来成交或未处理公司行为时整次拒绝。
- 容量规则的 ADV 固定使用截至计划日的 20 个可信未复权成交额观测，至少 15 个有效观测；不足时 fail-closed。
- `POST /api/v1/portfolio/plans/{plan_id}/risk-evaluations` 只接受账户和净值快照引用，服务端重跑 readiness 并绑定账户、市场、净值、ADV 与固定政策哈希；`bootstrap/gap` 或观察不足阻断风险增加订单。

阶段门：

- 任一候选订单可回放服务端权威风险输入和逐规则结论；评估本身仍不可执行，只有绑定它的短时确认可被 paper execution 单次消费。

### V5.3 模拟盘与持仓推演

状态：已完成最小公共 paper 执行闭环。模拟盘在本项目语义下是**持仓推演与风险验证工具**，用于评估调仓动作在含费、含可交易性约束下的结果，不连接任何券商或真实账户。

交付：

- 模拟订单、模拟成交、模拟持仓。
- QMT/MiniQMT 仅作为数据采集来源接入，不作为交易通道；不实现券商执行回报适配。
- `BrokerAdapter` 仅隔离本地模拟执行回报，账户现金和持仓由模拟盘服务统一更新；不引入真实券商适配器。
- `POST /api/v1/risk-evaluations/{risk_check_id}/confirmations` 仅接受风险评估哈希和动态确认短语，生成五分钟有效的 paper-only 意图；服务端绑定完整评估、账户、净值、readiness、ADV、政策和 sell-first/buy-second 订单批次哈希。
- 确认状态限定为 `pending -> consumed | expired | revoked`；创建、撤销均幂等，确认本身固定不创建订单或成交。
- `POST /api/v1/paper-execution-approvals/{approval_id}/executions` 只接受 approval hash，锁定确认与账户后重验评估、账户、市场、净值、ADV、政策和订单批次哈希；同幂等键重放同一结果，同 approval 最多一个 execution batch。
- 风险评估、确认和执行显式绑定当前代码版本；订单批次 Decimal 先规范化再哈希，成交价/佣金、现金和平均成本分别按数据库精度统一 ROUND_HALF_UP，避免 ORM 写入前后同值字符串差异造成错误漂移或审计不平。
- 执行固定为本地确定性 full-fill、卖先买后和全批原子语义；所有订单先预演，任一现金、持仓或保守 T+1 可卖量阻断都零成交。卖出前要求当前持仓可由历史 paper fills 完整解释，未知来源持仓 fail-closed。成功后确认消费、订单、成交、现金和持仓在同一事务提交。
- 公共 paper 链路已接入执行日可交易性检查：确认阶段由 `PaperExecutionApprovalService` 读取 `market.daily_bar` 后调用 `AShareTradabilityPolicy` 预飞；执行阶段由 `PaperExecutionService._assert_execution_day_tradable` 同样强制执行；任一 `missing_execution_day_bar`、`buy_blocked_by_execution_day_tradability` 或 `sell_blocked_by_execution_day_tradability` 都会以 `status=rejected` 落库并记录 `execution_day_bar_hash`，与回测共享同一份 A 股可交易性规则。
- `GET /api/v1/paper-accounts/{account_id}/ledger-audit` 可从既有 paper 订单、成交和 completed execution batch 递推核对现金，并从全量成交重建持仓数量与平均成本；报告只读差异，缺少执行批次现金锚点时明确标记 `unverifiable`，不会自动修复账户或持仓。
- Vue3 工作台在确认和执行前展示方向、数量、预计价格、费用和现金结果；仅对有效 pending 确认展示模拟执行按钮，二次确认明确不会发送真实订单。完成后展示现金与逐单成交并要求重新采集净值；预演拒绝则明确账户未变化。可按确认 ID 恢复超时或刷新后的执行结果。

### V5.4 真实持仓账本与持仓分析（2026-10-23）

状态：已完成首个切片。用户在外部券商终端实际成交后手工记账，项目第一次拥有真实仓位的事实源；分析全部只读、读时推导，不落估值结果。

交付：

- 真实持仓账本存独立 SQLite `data/positions.db`（存储边界修订见 `aiDoc/relations/20261023-1925-position-ledger-sqlite.md`）：`position_book`（账本/现金）、`position_trade`（买卖流水，整数最小单位金额 + 幂等键）；持仓数量、平均成本（含买入费用）、已实现盈亏由流水按 `(trade_date, id)` 重放推导。
- 持仓分析 API：`GET /api/v1/position-books/{book_id}/holdings` 输出逐仓成本/现价/浮盈/收益率/权重、集中度（HHI/Top5/最大单票）与行业暴露；估值取 DuckDB `market` 最新可信未复权收盘，缺价按成本估值并标记 `price_stale`。
- 模拟盘只读分析：`GET /api/v1/paper-accounts/{id}/positions`、`GET /api/v1/paper-accounts/{id}/performance`（净值曲线 + 累计/年化收益、最大回撤、日波动率；快照含 gap 时显式标记 approximate）。
- 偏离检测：`GET /api/v1/portfolio/plans/{plan_id}/drift?position_book_id=|paper_account_id=` 比较草案最终权重与真实/模拟持仓权重，输出增持/减持/持有方向与整手目标数量建议，不生成订单。
- 前端新增「持仓分析」页（账本管理、记一笔、持仓分析、行业暴露、偏离检测）；模拟盘页内嵌持仓与净值绩效面板；组合草案支持「从持仓账本预填」。

阶段门（沿用阶段 2 验收，未达成不勾选）：

- [ ] 连续 3 个月全自动模拟推演运行
- [ ] 每日再平衡结果可解释
- [ ] 真实成本下绩效可接受（夏普 > 0.5，回撤 < 25%）
- [ ] 失败订单有明确原因记录

明确不在本切片：自动再平衡触发器（调度扫描偏离超阈值提示）留待阶段 2 后续切片；账本不含券商连接或真实账户只读同步（永久边界）。

阶段门：

- 模拟盘公共操作链路、失败恢复、重复请求和审计稳定后，即可作为持仓推演的稳定基线。
- 常规单元/API 测试覆盖全批成功、幂等回放、T+1、未知持仓血缘、现金阻断、恢复查询和写入异常；显式 PostgreSQL 集成测试覆盖第二笔写入后回滚与双会话同键并发，默认无 `TEST_DATABASE_URL` 时跳过。
- 模拟执行结果只作为持仓决策参考，不得表述为可自动下单或已验证实盘可行性。

## V6 可视化和运维

目标：让数据质量、因子运行、回测结果、组合状态可观察。

状态：V6.1 只读运维工作台已具备阶段性收口条件：已完成最小运维概览接口、告警候选摘要、按分类限量的失败定位摘要、单项失败详情、只读重跑计划和 Vue3 运维工作台；前端已为支持的重跑计划展示执行前准备态。后端重跑执行入口、审计、幂等、权限和人工确认边界仍未完成，属于后续写侧运维动作设计，不应作为 V6.1 收口阻塞项。

交付：

- 数据质量看板：缺失、冲突、修复、来源健康度。
- 因子看板：覆盖率、分布、IC、换手。
- 回测看板：净值、回撤、持仓、归因。
- 同步和任务看板：最近运行、失败原因、重跑入口。
- `/api/v1/operations/overview` 聚合质量问题、任务日志、因子运行、回测运行和模拟盘状态。
- `/api/v1/operations/alerts` 汇总当前告警候选，并返回触发规则和建议人工动作；它仍只是只读运维提示，不触发通知、处置或重跑。
- `/api/v1/operations/failures` 按分类返回最近 N 条未解决质量问题、失败任务、失败因子运行、失败回测和被拒模拟盘订单，并提供各分类 `total`、`limit` 和 `has_more`，用于定位数据源、任务、证券、日期、错误原因和风控拒单规则。
- `/api/v1/operations/failures/{failure_type}/{failure_id}` 返回单项当前失败详情；仅支持仍处于未解决、失败、部分失败或被拒状态的记录，不触发状态变更、重跑或交易动作。
- `/api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan` 返回只读重跑计划；仅为可安全执行的后续动作提供建议，不触发任何重跑。
- Vue3 前端已通过 `frontend/src/App.vue` 和 `frontend/src/components/operations/` 提供运维工作台首屏，可在后端不可用时回退到 demo 数据；支持的重跑计划仅展示参数核对和禁用的执行入口占位，不触发后端写操作。

阶段门：

- 每日同步失败能被快速定位到数据源、任务、证券或日期。已具备概览、告警候选、按分类限量的失败摘要、单项详情、只读重跑计划和前端执行前准备态，细粒度重跑执行入口后续增强。
- 关键任务具备日志、指标和告警候选。已具备查询底座，并覆盖运行中任务、失败任务、失败/部分因子运行、失败回测、拒单和挂单等候选；告警规则后续继续增强。

## 选股与持仓管理终态

**本项目到此为止，不进入实盘自动化交易。**

Stock Master 的交付终点是「研究结论 + 选股名单 + 组合持仓方案 + 风险预算 + 持仓推演」。系统不连接券商、不实现真实账户只读或写操作、不发送任何真实订单，实际下单由人工在外部券商终端完成。

明确不在项目范围内的能力（不作为后续路线，也不设阶段门）：

- 真实账户只读同步与券商回报对账。
- 受控交易、自动下单与外部订单幂等状态机。
- 应急开关、全局停单与账户隔离。
- 券商可交易性规则与交易所级撮合语义。

模拟盘（paper）是本项目的**终态持仓推演工具**，用于在含费、含可交易性约束下评估调仓动作的结果，不是通向真实账户的过渡阶段。任何模拟执行结果都只作为人工持仓决策的参考，不得表述为可自动下单或已验证实盘可行性。

## 暂缓事项

在 V2/V3 稳定前暂缓：

- 分钟线。
- Tick。
- 高频交易。
- 完整财务数据库。
- 大规模参数优化。
- 复杂多页面前端。

## 下一步实施决定

V4 的“单策略、单参数集、单次回测”研究合同闭环已经交付。现有研究入口会规范化并保存策略、因子、数据、复权、调仓、持有期、费用、滑点、信号和代码版本，运行详情可以直接还原合同。

本切片已完成：通过现有研究 API 创建、运行和查询一次回测；结果可追溯到完整输入版本；确定性排序和回归测试覆盖重复运行所需的稳定输入；失败运行保留状态与错误信息。

原单次回测切片明确不做批量、比较和验证编排；这些后续支撑 API 已分批交付，但不改变原计划边界，也不代表训练与选参已经自动化。

详细执行计划见：[V4 单策略研究合同与可复现回测计划](v4-research-contract-plan.md)。

V4 已交付研究合同及一组可复用支撑 API：`out_of_sample` 运行强制训练期早于测试期，仅在测试区间取因子和回测，并记录 `holdout_v1`、显式测试边界、确定性 `validation_window_id`、`training_artifact_verified=false` 与 `window_binding_status=recorded_only`；按交易所开市日生成的滚动窗口带确定性 `window_id`，但固定标记为 `planning_only`。批量运行、基准和跨运行比较可查询，同合同回归漂移诊断仅允许研究合同、测试区间和数据版本一致的运行，阈值按收益率和回撤率的绝对差值解释。滚动执行在不可变训练/选参产物及窗口到运行绑定落地前明确拒绝，不能据此宣称完整 walk-forward 已完成。V6.1 只读详情/失败定位已具备收口条件，下一步主线转入 V5 公共操作闭环。

## 推荐近期执行顺序

价值初筛当前切片：自动日线同步已恢复，HiThink 证券上市日期通过 raw 留痕及有限重取获得高覆盖；同日晚间东方财富估值支持日期接口退回，披露 ROE 可按已发布报告期批量获取并逐页留 raw，网页显示沪深北有效指标、来源及阻断原因。2026-09-23 首轮真实晚间准备成功，估值同价匹配 5,556 只、披露 ROE 5,517 只；随后报告覆盖 5,578 只，但 ST 状态均未核实，0 只通过准入门槛。这是数据缺口，不是低估值股票为零。旧晚间任务写入的 5,552 条通用 ROE 因子已归档并失效（run 356），新的研究观察值与通用因子隔离。行业、ST 完整时点属性与财报修订历史仍不足，不能宣称已有可交易或历史收益认证的全市场名单。下一门槛是连续交易日运行、逐所价格/ROE/属性覆盖核对、未知 ST 的可信来源及历史 PIT 隔离。

1. 研究可信基线修复：恢复温层历史日线继承、重建 `market.prices_qfq` 视图、回测报告与落盘数字对账、研究产物纳入版本控制。
2. V4 训练选参编排、产物血缘、窗口绑定和参数敏感性。2026-10-24 进展：训练选参首切片（自演进阶段 1）已交付——`selection_policy=training_ic_v1` 逐训练窗自动选因子与方向，产物血缘与窗口绑定此前已在，参数敏感性与 champion/challenger 晋级（自演进阶段 2-3）仍待后续切片，见 [训练窗自动选参计划](20261024-1522-training-window-selection-plan.md)。首次实跑完成端到端对照（seg2022 六窗：自动选参 -0.13% vs 静态 ic_aligned -0.23%，2024-2026 段逐窗证据触发 fail-closed 拒绝）——静态预设的前视嫌疑被逐窗证据量化，结论见 [首次实跑证据](../research/20261024-1706-training-window-selection-first-run.md)。同日候选池扩容 13→16 个价量因子（新增 `abnormal_volume_pressure`/`beta_60`/`residual_volatility_60`，等权 stock/etf 市场收益口径）：seg2022 段默认门槛（存活≥3）首次全程可用且均值转正（+2.43%/窗、最差回撤 -17.95%），2024-2026 段仍 fail-closed（2/16 存活）——价量族证据强度已到上限，该段过门槛需 P2-5 估值历史，见 [扩池首跑证据](../research/20261024-1822-pool-expansion-price-volume-ext.md)。
3. V5 组合、风控和模拟推演的公共 API、前端操作流、人工确认和审计。
4. V6 写侧运维动作设计：重跑执行入口需先完成权限、审计、幂等、dry-run/confirm/execute 和人工确认边界。
5. 选股与持仓管理能力增强：因子 IC 证据表、分层回测、组合约束进回测、偏差源补齐。2026-10-23 进展：非日频调仓（weekly/monthly/N 交易日）与可选组合约束（单标/行业/类型/现金留存，经 PortfolioBuilder 与 paper 链路同源）已进入因子选股回测路径，行业标签暂用当前值，时点历史与风格预算仍待 P2 数据项。2026-10-24 进展：分层回测（`quantile_backtest`，按 split 因子决策日横截面分桶、逐桶等权多头、top–bottom 统计差）与多窗口组合净收益曲线拼接（跨窗归一化本金、重叠窗 fail-closed）已交付；2026-10-25 已修正分层首版“离桶旧仓不退出”和“仅加载主策略证券行情”的 P0，只有带 `explicit_zero_exit_v2` 与独立 `quantile_data_snapshot` 的新分层结果可用于研究解释——见 [分层回测与组合曲线](../research/20261024-1800-quantile-and-rolling-curve.md)。温层复跑验证：同一 `ic_aligned` 组合 20 交易日调仓净收益 +12.36%（日频净 +0.08%，佣金 -86%，夏普 1.42），叠加组合约束净 +11.72%、回撤更浅——频率敏感性证据见 [调仓频率净收益证据](../research/20261024-1205-rebalance-frequency-net-return.md)；rolling walk-forward 频率效应跨窗一致（2/2 正窗）。P2-1 涨跌停回填（控制面 97%）与 P2-3 行业时点首切片已落地。研究→持仓闭环已打通：`GET /backtests/runs/{id}/final-holdings` + 草案「从回测持仓预填」把最优配置的期末持仓一键变成组合方案（再经偏离检测对照实际账本）。剩余：偏差源补齐（幸存者/ST/停牌/行业时点/公司行为会计）仍属硬缺口，分层与回测结果均带声明，不扣这些偏差。

详细执行计划见：[研究产出最大化行动计划](20261021-1708-research-output-maximization-plan.md)。
