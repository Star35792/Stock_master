---
doc_type: decision
status: partially_superseded
as_of: 2026-10-23
superseded_by:
  - relations/20261024-1427-research-database-split.md
  - plans/20261119-1049-agent-overall-plan-and-sqlite.md
note: >-
  持仓账本使用独立 SQLite 的决定继续有效；其余域均在单 DuckDB、SQLite 为唯一例外的全局表述，
  已由研究分库和后续智能体 SQLite 状态库决定扩展。
---
# ADR：真实持仓账本采用独立 SQLite 存储

- 日期：2026-10-23
- 状态：已接受（用户决策）
- 决策级别：T2（存储架构边界变化）

## 背景与触发原因

2026-10-23 用户要求新增「持仓管理和分析功能」，并明确指定真实持仓数据使用 SQLite 存储，其余数据继续使用 DuckDB。此前 `20260722-1721-tiered-storage-architecture.md` 记录了「不引入 SQLite」的评估结论，本次决策对该边界做了一次受控修订。

## 决策

1. **新增 `positions` 域**，存放「手工录入的真实持仓账本」：用户在外部券商终端实际成交后手工记账。这是「持仓管理」核心能力的数据基础，此前项目只有模拟推演账户（`trade.paper_*`），没有真实仓位的事实源。
2. **存储边界**：
   - 真实持仓账本 → 独立 SQLite 文件 `data/positions.db`（`settings.positions_db_path`），表 `position_book`、`position_trade`，金额用整数最小单位（0.0001 元的 quants）保存，满足「核心金融数据不用浮点」的项目规则。
   - 其余一切（raw/market/factor/strategy/portfolio/risk/trade/lake/metadata）保持 DuckDB 单库不变；温层 Parquet 不变。
3. **估值口径跨库但同源**：账本持仓的估值价格取 DuckDB `market` 可信层最新未复权收盘（trusted/manual_fixed、非 suspect），与 paper 权益快照同一纪律。SQLite 只存事实（流水），不存估值结果。
4. **只读分析 + 建议式偏离**：持仓盈亏/集中度/行业暴露全部实时推导，不落库；组合草案偏离检测（`GET /portfolio/plans/{id}/drift`）输出方向与目标数量建议，不生成订单。持仓分析允许缺价时按成本降级展示并标记 `price_stale`；偏离建议会用持仓市值形成全组合权重，任一非零持仓缺可信价格时必须整体返回 422，不能让成本价污染总权益、现金权重与增减持方向。
5. **边界不变**：不连接券商、不做实盘自动化交易、不引入真实账户只读适配器。账本靠人工记账，卖出校验净持仓非负，删除流水会重放推导。

## 为什么 SQLite 合理（相对原评估的修订理由）

- 原评估拒绝 SQLite 的场景是「替代 DuckDB 承载数据中心」；本次用途是单用户、低频写入、纯事实型的个人账本，体量极小且与数据中心生命周期独立（可单独备份/迁移/清空，不影响行情与因子数据）。
- 持仓账本与市场数据的事务一致性不需要跨库原子性：估值是读时推导，行情缺失时按成本估值并显式标记 `price_stale`。

## 影响

- 新增 `backend/app/database/positions_base.py`、`positions_session.py`（独立 DeclarativeBase + engine + `get_positions_db` 依赖）；`app/models/positions/`；`app/repositories/positions/`；`app/services/position_book.py`、`position_metrics.py`、`portfolio_drift.py`、`position_analysis.py`。
- API 新增：`/api/v1/position-books*`（账本 CRUD + 记账 + 持仓分析）、`GET /api/v1/paper-accounts/{id}/positions|performance`、`GET /api/v1/portfolio/plans/{id}/drift`。
- 前端新增「持仓分析」页（`/positions`）；模拟盘页新增持仓与净值绩效面板；组合草案支持「从持仓账本预填」。
- 测试：conftest 增加内存 SQLite override（StaticPool）；duckdb `:memory:` 在 duckdb-engine 下是按线程隔离的 SingletonThreadPool，请求线程内的真实 DuckDB 查询须 monkeypatch（已记录踩坑）。
- `AGENT.MD` 与 `system-map.md` 的存储描述同步修订；`schema-plan.md` 增补 positions 表域。

## 2026-10-23 T2 评审记录

四角色评审（架构/金融/后端/产品）通过后提交，两个 P0 已修复：

1. 草案预填现金双计（预填把总权益填进现金字段，与已预填的当前数量叠加放大目标数量）——已改为填账本现金。
2. 乱序流水的 `sequence_anomaly` 计算后未暴露，已实现盈亏会被静默高估——已在 holdings 响应与前端暴露警示。

已知 P1（记录在案，后续优化）：

- 年化收益要求 ≥30 天观察期（已随 P0 修复顺手收紧）；365 日历年化与 √252 波动率口径混用，前端暂不展示年化。
- ~~HTTP 层测试缺口：`PATCH /position-books/{id}`、DELETE trade、drift happy path 未过 HTTP~~（2026-10-23 当日已补齐：PATCH/DELETE/DELETE-回滚/drift 双库接线均有 HTTP 覆盖）。
- ~~并发同幂等键双插会以 500 兜底而非 409~~（已修复：IntegrityError 捕获后按同键匹配重放或 409，并有单测）。
- ~~清仓残余成本尾差直接清零不进已实现；预填固定取第一个账本，多账本时无选择器~~（已修复：尾差计入已实现且清仓后账本级已实现盈亏不再消失；预填支持多账本下拉选择）。
- 其余 P2：前端幂等键失败重试已换新键（已修）；`quant_db` 措辞已更新（已修）；drift 整手为 0 时补充 reason（已修）；paper 估值逻辑已提取 `build_cost_basis_valuations` 共享（已修）。
