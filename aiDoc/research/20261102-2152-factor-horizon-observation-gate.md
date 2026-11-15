---
doc_type: decision
status: current
as_of: 2026-11-02
---
# 因子多周期有效观测数资格闸门

## 结论

`factor.factor_profile` 的因子池汇总除总画像天数外，现分别统计 1/5/20 日 Rank IC 的非空观测数，并继续统计 1 日端点多空价差的非空观测数。核心池要求四项计数均不低于 `FactorPoolPolicy.min_trade_dates`；均值达标但任一证据只有少量有效日期时，因子最多留在观察池。

该变更只修复“稀疏 horizon 均值冒充长期证据”的资格缺口。它不修改 `factor_profile` 表、唯一键、DuckDB/Parquet/SQLite 存储结构或历史行，也不调整 IC、端点收益和复权公式。

## 触发原因

SQL `avg(column)` 会忽略空值。旧汇总用 `trade_date_count` 判断窗口长度，却没有记录三个 Rank IC horizon 各自进入均值的日期数；1 日端点虽有计数，分类器也只要求大于零。因此总画像达到 200 天时，单个 horizon 或端点只有 1 天非空值仍可能满足核心池数值门槛。

## 口径

- `trade_date_count` 继续表示评估窗口内落库的画像行数。
- `rank_ic_1d_observation_count`、`rank_ic_5d_observation_count`、`rank_ic_20d_observation_count` 分别使用 `COUNT(rank_ic_*)`，只统计非空值。
- `endpoint_observation_count` 仅表示 `long_short_forward_return_1d` 的非空日期数。
- `min_trade_dates` 同时约束总画像行数和上述四项有效证据数；计数恰好等于阈值时通过该门槛。
- 最新窗口尚未成熟的 5/20 日远期收益保持空值，不得被当作有效证据。调用方应扩大可观测历史窗口，而不是降低或虚构计数。
- 旧调用方未提供新计数字段时默认 0，按 fail-closed 处理。

汇总 API 与因子池分级响应增加三个 Rank IC 计数字段；CLI JSON 同步输出四项计数。既有字段不删除、不改名。

## 资格边界

观测数门槛不等于统计显著性，重叠的 20 日远期收益也不是同等数量的独立样本。因子 release、价格输入快照、完整日期身份和输入哈希仍未绑定；PIT、公司行为、历史 ST/行业、退市样本、幸存者偏差、费用与样本外验证仍按既有门槛处理。本修复不能单独恢复任何因子的策略资格，也不能把核心因子池解释为可交易策略。

## T2 评审与回退

架构、产品与金融独立评审均判定旧行为是 P0 因子资格正确性缺口，并有条件通过“现有查询聚合计数 + 复用 `min_trade_dates` + API/CLI 可见 + 不改存储”的最小方案。Vibe Coding 规则审查确认变更保持 service/repository 分层、补测试和文档，并与工作区其他在制改动隔离提交。

回退只需撤销聚合字段、只读 DTO/CLI 输出和分类门禁；没有数据迁移或历史数据改写。
