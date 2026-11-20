---
name: position-risk-review
description: 检查用户指定book_id的人工持仓报价完整性、集中度与行业覆盖。用于持仓风险提示，保留缺价、陈旧报价及流水异常。
---

先明确 book_id，再只读 position_holdings；缺ID时可用 research_overview 列出账本供用户选择。

核对报价日及逐仓日期、证据中的missing_price_count、汇总与逐仓price_stale、流水异常、原服务 concentration 和 industry_exposure。空仓可解释；缺价或日期混用须明示，不自行把缺值填零或重算风险指标。

说明集中度与行业覆盖的原值及限制。当前工具不代表完整风险模型，不能根据一次检查认定持仓安全；不写账本，不自动生成或执行调仓。

结束前调用 verify_skill，resource_id 为用户指定的 book_id。根据缺失检查项补查，无法取得原证据时说明未完成。
