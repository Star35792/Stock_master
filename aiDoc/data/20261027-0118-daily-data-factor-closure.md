---
doc_type: evidence
status: partially_superseded
as_of: 2026-10-27
superseded_by:
  - relations/20261111-1810-industry-point-in-time-history.md
note: >-
  数据→因子闭环链路继续有效；其中行业族按当前标签口径的部分已被时点区间交付取代。
---
# 2026-10-27 日线与因子数据闭环证据

## 1. 结论

本轮把可由现有上游支持的日级链路闭合到最近已完成交易日 **2026-10-26**：

```text
HiThink 全市场 none dump
  -> raw.daily_bar
  -> market.daily_bar
  -> market.adjustment_factor (qfq, hithink_finance_dump@v1)
  -> curated/prices_qfq
  -> factor.factor_value
  -> warm release 20261027-data-close-v2
```

闭环不等于“所有数据域均已具备”。指数日线、宏观、政策、盈利预期、当日估值输入及独立拥挤度输出仍有上游缺口，详见第 5 节；这些族被明确记为 `empty/partial`，没有用零值或旧值伪造成功。

## 2. 行情与复权验收

- `market.daily_bar(none)` 完整历史：10,360,649 行，2016-10-26 ~ 2026-10-26，业务键重复 0，OHLC 非法行 0。
- 2026-10-26 active 且已上市股票：5,585；可信 bar：5,568；覆盖率 **99.6956%**。
  - BJ：353 / 356（99.1573%）
  - SH：2,314 / 2,321（99.6984%）
  - SZ：2,901 / 2,908（99.7593%）
- 2026-10-19、20、21、22、23、26 的股票日线数分别为 5,563、5,567、5,567、5,569、5,570、5,568。
- `market.adjustment_factor` 的生产 QFQ 口径固定为 `hithink_finance_dump@v1`；10,360,649 个业务键与 none 日线一一匹配，缺口 0。
- 历史批量导入的 adjustment surrogate `id` 可为空。发布认证已改为检查 `(symbol, trade_date, adjust_type, source_name, version)` 业务键是否存在，不能用可空 `id` 判断 JOIN 是否命中。
- `curated/prices_qfq`：10,696,598 行、6,149 个证券，2016-07-25 ~ 2026-10-26，`(symbol, trade_date)` 重复 0。

## 3. 因子验收

2026-10-20 ~ 2026-10-26 的全市场批量补算写入 742,298 行：

| 因子服务 | 写入行数 | 判定 |
| --- | ---: | --- |
| BasicPriceVolumeFactorService | 194,189 | success |
| FinancialFactorService | 302,098 | success |
| IndustryFactorService | 83,523 | success |
| SentimentFactorService | 84,422 | success |
| CapitalFlowFactorService | 78,066 | success |
| ValuationFactorService | 0 | empty：目标窗口无估值输入 |
| MacroFactorService | 0 | empty：无宏观输入 |
| PolicyEventFactorService | 0 | empty：无政策事件输入 |
| EarningsRevisionFactorService | 0 | empty：无盈利预期输入 |
| CrowdingFactorService | 0 | empty：依赖输入不足，未产出独立值 |

任务最终状态已从旧实现误写的 `success` 收敛为 `partial`，metadata 明确列出 5 个 `empty_services`。全部 active 定义共 81 个，其中 39 个定义/版本已有历史值，42 个仍为空；这 42 个是上游或实现边界，不是本轮计算失败。

目标日 7 个生产价量因子均超过 95% 股票覆盖闸门：

| 因子 | 股票数 | 相对 5,568 只可信股票覆盖率 |
| --- | ---: | ---: |
| momentum_20 | 5,553 | 99.73% |
| momentum_60 | 5,518 | 99.10% |
| volatility_20 | 5,553 | 99.73% |
| downside_volatility_20 | 5,553 | 99.73% |
| max_drawdown_20 | 5,553 | 99.73% |
| liquidity_20 | 5,553 | 99.73% |
| volume_stability_20 | 5,553 | 99.73% |

`factor.factor_value` 业务键重复为 0。目标日有值的 active 因子身份覆盖价量 7、财务健康 4、基本面质量 10、行业 3、情绪 7、资金流 3，共 34 个。

## 4. 发布、恢复与存储

- 可用温层 release：`20261027-data-close-v2`，`all_ok=true`；父链为 `20261026-valhist-20240501 -> 20261024-selfevo3ext16 -> 20261021-1800`。
- release 读取验收：
  - `market.daily_bar`：10,360,649 行，业务键重复 0；
  - `factor.factor_value`：8,865,894 行，2024-05-06 ~ 2026-10-26，业务键重复 0；
  - `research.valuation_history`：3,226,685 行，2024-05-06 ~ 2026-10-19。
- 同 tag 重跑曾暴露 `OVERWRITE_OR_IGNORE` 会留下旧 Parquet 文件。失败重跑 `20261027-data-close` 已被行数校验标成 `all_ok=false`，不会成为事实源；导出脚本现对已存在 release table fail-closed，必须使用新 tag。
- release 表新增 `snapshot/delta` 模式。完整快照停止继承同表祖先；delta 按业务键由新 release 覆盖旧 release，避免父子层重复。
- 控制面按 2026-07-28 起的热窗口收缩：3,355.26 MiB -> 717.51 MiB（减少 78.6%）；完整历史由温层承载。
- CTAS 收缩会丢失序列/default。初始化与收缩脚本现会按各表现有最大 ID 重建 40 个模型序列并恢复列默认值；任务写入探针成功取得新 ID 后回滚。
- 历史遗留的 3 个 `metadata.job_log.running` 与 5 个 `raw.import_batch.running` 已在确认无存活 owner 后改为 `failed`；当前两表 running 数均为 0。

## 5. 明确未闭合的数据缺口

1. 指数日线仍停在 2026-10-14；2026-10-26 指数 bar 为 0。股票覆盖闸门与指数覆盖分开报告，不能用股票完成度掩盖指数缺口。
2. 估值 active 定义中 5 个有历史值，但最新值停在 2026-10-19；目标补算窗口 `ValuationFactorService` 无输入。估值历史研究表全部 `pit_verified=false`，PB_MRQ 仍含前视风险。
3. 宏观 7、政策 1、盈利预期 5 个 active 定义仍无值；资金流仅 3/11、情绪 7/11、财务健康 4/6、基本面质量 10/14 有值。
4. `market.security_status_event` 和 `market.security_industry_history` 仍无历史时点事实；当前 ST/行业标签不能倒填历史。
5. 本轮只闭合数据与因子事实，不产生新的 IC/profile；2026-10-26 因子尚无未来 1/5/20 日收益，不能提前生成有效性证据。

## 6. 自动闭环保护

- 自动全市场 dump 以 `started_at` 而不是可能重建回退的序列 id 判断最近成功水位。
- 最新日股票覆盖低于 95% 时不推进同步水位。
- 行情水位已到但该日没有 `publish_eligible=true` 的 `daily_loop` 时，后续轮次仍会重试，不会因“行情已同步”跳过因子发布。
- curated 先写临时目录，因子直接读取该 staging；数据、QFQ、重复键与 7 个核心价量因子覆盖闸门通过后才原子切换正式目录。
- 零行服务记为 `empty`，因子批任务和日级闭环记为 `partial`，不再显示为完整成功。

## 7. 后续增量补齐

自动采集闭环的日级价量批任务现显式计算全部 16 个可信 QFQ 日线可支持的价量因子，包括既有 7 个核心项及 `ret_1d`、`momentum_5`、`abnormal_volume_pressure`、`reversal_5`、`beta_60`、`residual_volatility_60` 等扩展项。服务独立调用的默认清单仍是 7 个。日级认证记录 16 项逐因子覆盖率及不足项，原 7 个核心项仍为硬发布闸门；扩展项的覆盖不足会在日志中显示，待实际跑数验证后再决定是否升级闸门。闭环日志记录因子清单版本与完整名单；旧版已发布闭环会在下一轮自动采集时重跑当前目标日。此处是计算范围调整，尚未执行历史回填或新增项覆盖验收，不能将定义数变化当作已有值或有效性证据。`turnover_rate`、`turnover_percentile_120` 因温层缺少可信换手率历史暂不进入日级计算，拥挤度也不得标为已补齐。
