---
doc_type: decision
status: current
as_of: 2026-11-17
---

# 组合草案旧主库列兼容

## 现状与决策

2026-11-17 只读核查发现本机主库的 `portfolio.portfolio_plan` 和 `portfolio.portfolio_target` 均无记录，但分别缺少当前 ORM 所需的 `style_risk`、`score` 列。草案摘要列表通过限定加载列可正常读取；创建、完整详情和偏离检测仍会触及缺列。此前的[草案发现决策](20261117-0129-portfolio-plan-discovery.md)所述“无 schema 迁移”仅适用于列表接口本身，不能覆盖整个日常流程。

沿用 `app/database/session.py::apply_schema_compatibility` 的显式兼容桥：在旧表存在且列缺失时，分别补充 nullable 的 `portfolio_plan.style_risk JSON` 和 `portfolio_target.score DECIMAL(18,10)`。每列独立提交，重复执行不改已有列或值；旧行保持 NULL，不伪造历史风格风险与评分。新库仍由 ORM `create_all` 建表。

## 验证与回退

带旧草案与目标记录的临时 DuckDB 测试覆盖重复升级、旧行空值，以及真实仓储的新草案与评分写入回读。对本机主库在写锁内执行了这两条定向增列语句并完成 `CHECKPOINT`；重新只读打开后，列类型为 `JSON` 与 `DECIMAL(18,10)`，草案/目标行数升级前后均为 0/0，真实主库上的 `GET /api/v1/portfolio/plans` 返回 200 和空列表。未创建样例用户草案。增列属于持久 schema 变化，不通过删列自动回退；异常时停止服务并按数据库证据诊断。

这项升级只恢复草案记录的存取能力，不改变组合风险计算、可信行情资格或人工下单边界。草案截至日、预算与当前账本/报价日期仍须人工核对。
