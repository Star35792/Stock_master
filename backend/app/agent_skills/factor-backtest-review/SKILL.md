---
name: factor-backtest-review
description: 解读指定run_id的既有因子回测，核对研究合同、绩效、成本与证据限制。用于回测表现解释，不自动重跑或认证策略。
---

先明确用户指定的 run_id，再读 backtest_result。没有ID时可用 research_overview 展示候选并补问，不能代替用户选运行。

按原服务核对状态、起止日期、代码/数据版本、费用及执行假设、原绩效指标与期末持仓。需要因子含义时查 factor_definitions，目录分页不足不能推断定义不存在。

读取 [证据解读](references/evidence.md) 处理缺失指标或资格问题。解释分别列事实、缺失及推断。不能自行重算指标、把任务成功当作研究有效，或因收益不佳就调整参数重跑。

结束前调用 verify_skill，resource_id 使用用户指定的 run_id；按服务端反馈补查或说明材料不足。
