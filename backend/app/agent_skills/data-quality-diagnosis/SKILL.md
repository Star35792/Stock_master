---
name: data-quality-diagnosis
description: 诊断现有数据质量与任务异常，区分问题样本、整体统计及需要另行规划的修复。用于数据缺口、质量告警、采集失败的检查。
---

使用 operations_overview 获取概览统计与生成时刻，再用 quality_issues 读取 open 问题样本。针对用户明确证券可补查 market_summary。

解释 issue_status_counts 与 open_issue_severity_counts 的口径；quality_issues 只筛 open，概览严重度计数包含 open/reopened，最近问题样本可含其他状态。保留 total/limit/offset，不能把首批样本当全量，更不能用零条样本推出可信层已认证。

发现问题本身不表示诊断失败。输出观察范围、实际问题及原ID/日期、数据缺失和可选修复路径；需要写入时转为新计划，经原预检和逐项确认。

结束前调用 verify_skill 核对本轮证据。缺少必要工具结果时按反馈补查，重复失败就说明未完成及原因。
