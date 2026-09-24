# 项目记忆 Reference

本文件只记录跨任务稳定有效的事实、偏好和踩坑经验。不要记录一次性过程、临时计划或未验证推测。

## 稳定事实

- 用户希望 vibe coding 时由 AI 主动维护产品、架构、代码规则、计划和记忆文档，不要让用户手动追文档。
- 项目文档采用 `AGENT.MD` 作为 AI 协作规则唯一真源，`aiDoc/` 作为唯一项目文档目录，同时承载正式设计和轻量 reference。
- 当前项目已经进入较大体量阶段，默认应通过 reference 精确读取相关文档，避免每次全量读取。
- 日级数据→因子闭环由 `app/scheduler/daily_loop.py` 编排：`daily_sync` 构建 `market` 后，若 `factor_reads_curated=true` 且 `factor_adjust_type='qfq'`，则从 `market.daily_bar` 和 `raw.corporate_action` 重建 `curated/prices_qfq` 温层，再运行 `factor_pipeline` 滚动重算因子；`system.job_log` 记录每次运行状态，sync 或 curated 重建失败时都不继续执行因子计算。

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
