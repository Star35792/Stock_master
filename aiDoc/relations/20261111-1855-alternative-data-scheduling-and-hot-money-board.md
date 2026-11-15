---
doc_type: decision
status: current
as_of: 2026-11-11
---
# 六族断供修复：把「没人去取」变成「每天去取」

> 日期：2026-11-11
> 关联任务：#11 六族 alternative 数据断供修复
> 状态：已交付并验证（调度接入 + 游资榜端点参数修正）

## 一句话

`market.alternative_data_snapshot` 的六族断供**根因是调度里根本没有这一步**——
采集服务一直存在、端点一直活着，只是没人调用。现在它进了 `daily_loop`，
**在因子管道之前**执行，失败降级为 `partial` 而不是让整轮失败；同时修掉一个
被误判为「不可修」的数据集：游资榜 `dragon_tiger_hot_money` 之所以恒为 0 行，
是采集端**漏传了 `board_type` 参数**，不是端点没内容。

## 断供的四类根因（此前混为一谈）

`app/services/alternative_data_supply.py` 的台账把断供拆成四类：

| 类别 | 含义 | 本项目实测 |
|---|---|---|
| 端点失效 | 端点长期无数据 | **不存在**（2026-11-11 全端点实测可用） |
| 从未被触发 | 服务存在但无调度入口 | **主因**：`daily_loop` 无 alternative 入口 |
| 空集被当成成功 | 归一化 0 行仍记 success | 已在采集层留痕（`empty_datasets`） |
| 从未接通 | 因子声明了依赖但无实现 | `failed_limit_up` 等仍属此类 |

物证：`raw.vendor_snapshot` 里每个 alternative dataset 恰好一条、全部停在
2026-09-18 ——「跑过一次之后再没跑过」，不是「跑了但没数据」。

## 一、调度接入（`app/scheduler/alternative_data.py`）

新增一个调度步骤，把两个采集源串起来：

* `hithink` → 情绪族（涨停池 / 连板梯队 / 热股 / 异动 / 龙虎榜 / 指数目录）
* `eastmoney` → 资金流族（主力资金 / 概念 / 行业资金流）

### 失败语义（本轮最关键的设计取舍）

> 采集依赖外部厂商网络，**瞬时故障不应该让整个日循环失败**——行情同步比它
> 重要得多。但也**不能静默吞掉**：吞掉就等于回到原样，断供再次伪装成成功。

因此：

| 情形 | 采集步骤状态 | 日循环任务状态 | reason code |
|---|---|---|---|
| 全部成功 | `success` | `success` | — |
| 某 dataset 归一化为空 | `partial` | `partial` | `ALTERNATIVE_DATA_EMPTY_DATASETS` |
| 某源抛异常 | `failed` | `partial` | `ALTERNATIVE_DATA_SOURCE_FAILED` |
| 采集器本身崩溃 | `failed` | `partial` | `ALTERNATIVE_DATA_SOURCE_FAILED` |

**单源失败不拖垮其他源**：东财挂了，同花顺情绪族的结果照样保留并入库。
**空集 ≠ 成功**：端点活着但归一化后没数据，报 `partial` 而不是 `success`——
这正是「端点可用」与「数据可用」必须分开判定的地方。

**为什么不硬失败**：行情发布闸门（`gate_status` / `publish_eligible`）与
alternative 快照互不相干，让厂商抖动阻断行情发布是本末倒置。六族因子照常按
**已有历史序列**计算——快照是历史序列，缺一天只会让最新一天陈旧，不会让整段失效。

**为什么不静默**：降级发生在 `repo.finish` 之前的最后一步，
`reason_codes` + `alternative_*` 元数据 + 独立 job 记录（任务名
`alternative_data_collection`）三处留痕。

### 接入位置：因子管道**之前**

六族因子（sentiment / macro / policy / earnings_revision / capital_flow /
crowding）读的就是 `market.alternative_data_snapshot`。晚采一天等于让这六族
因子白等一天。测试 `test_daily_loop_collects_alternative_data_before_the_factor_pipeline`
用调用顺序断言锁死了这一点。

### 实跑验证（2026-11-11）

```
hithink : 11 raw → 1,726 normalized rows
eastmoney: 3 raw → 300 normalized rows
status  : success（empty_datasets 为空）
```

同时验证了 DuckDB 嵌套写事务路径：`logged_job` 持有一个 session，采集源各开
一个 session，同库同文件顺序写入，实测无冲突。

## 二、游资榜：一个被误判为「不可修」的数据集

采集跑通后仍报 `empty_datasets = ('dragon_tiger_hot_money',)`。台账里写着：

> `status=EMPTY`，`blocker="端点 hot_money_items 恒为空数组"`，备注
> 「2026-09-17 抓取时即为 0 行，非断供而是端点该子集无内容」。

**这个结论是错的。** 端点用 `board_type` 选择返回哪张榜：

| board_type | stock_items | hot_money_items |
|---|---|---|
| `all` | 56 | **0** |
| `org` | 36 | **0** |
| `hot_money` | 0 | **11 个游资席位** |

采集代码调的是 `fetch_dragon_tiger_list()`（默认 `board_type="all"`），
然后从**这张榜的响应里**读 `hot_money_items` —— 永远是空数组。
「端点无内容」实际是「参数没传对」。

修正后实测（2026-11-10 榜单）：11 个席位、46 行、35 只标的，
`温州帮 / 山东帮 / T王 / 低位挖掘 / 小鳄鱼 / 深南东路 / 赵老哥 / 湖里大道 /
成都系 / 章盟主 / 玉兰路`。

### 两处必须做的配套处理

**1. 唯一键要带席位。** 同一只股票可能同时出现在两个游资席位下（实测 7 只）。
`item_key` 若只用 `thscode`，`(dataset, snapshot_date, item_key)` 唯一键会让
后一条覆盖前一条——席位归属静默丢失。改为 `{席位}|{thscode}`，实测落库
唯一键重复 **0**。

**2. 席位口径 ≠ 总榜口径，不得混用。** 实测同一只股票的 `net_rate`：

| 标的 | 游资榜行（席位口径） | 总榜行（龙虎榜口径） |
|---|---|---|
| 600609.SH | 0.10970192 / 0.08475541 | 0.08475541 |
| 002086.SZ | −0.002911077 | 0.02629938 |

且**席位净买额之和 ≠ 总榜的游资净买额**（35 只里只有 14 只相等）——
两个口径统计的是不同的东西，谁也不能替代谁。

因此 `dragon_tiger_net_buy_ratio` 因子**显式只用总榜**
（`datasets=("dragon_tiger",)`）。该因子在生产中本来就只有总榜来源（游资榜
一直是空的），所以**既有因子值一个都不变**，只是把口径钉死，防止游资数据
落地后这个因子在不同标的上悄悄换口径。游资数据留给将来的席位专用因子
（席位口径需要单独的 PIT 审计与 IC 证据，不能顺手塞进现有因子）。

## 三、改动清单

| 文件 | 改动 |
|---|---|
| `app/scheduler/alternative_data.py` | 新增：六族采集调度步骤 |
| `app/scheduler/daily_loop.py` | 接入采集（因子管道之前）+ `partial` 降级 + reason code |
| `scripts/scheduler/run_daily_loop.py` | 输出 `alternative_status` / `alternative_error` |
| `app/services/hithink_alternative_collection.py` | 游资榜改用 `board_type=hot_money`；席位拍平 + 席位键 + 席位口径取值 |
| `app/services/alternative_data_supply.py` | `dragon_tiger_hot_money` 由 `EMPTY` 改 `CONNECTED`，删掉错误的 blocker |
| `app/services/sentiment_factors.py` | `dragon_tiger_net_buy_ratio` 口径收敛到总榜 |
| `tests/test_alternative_data_scheduler.py` | 新增 5 例（单源失败 / 空集 / 未知源 / 成功 / 任务名） |
| `tests/test_daily_loop.py` | 新增 5 例（顺序 / 源失败 / 空集 / 崩溃 / 可跳过） |
| `tests/test_hithink_alternative_collection.py` | 新增 2 例（席位拍平 / 席位键与席位口径） |
| `tests/test_sentiment_factors.py` | 游资榜不再进入龙虎榜净买比例因子 |

## 四、验证

* 全量 pytest：**1,413 passed**（516 + 897，两批跑），零失败零错误
* ruff `app scripts tests` 全绿
* 实跑采集：`status=success`，游资榜 46 行 / 35 标的落库，唯一键重复 0

## 五、遗留

1. **六族 20 个因子的 `pit_status` 认证**（任务 #11 最后一项）：sentiment 11 /
   macro 7 / policy 1 / earnings_revision 5 / capital_flow 12 / crowding 3
   目前全是 `pit_status='unknown'`，被因子池分级器 fail-closed 挡住，
   **补了数据也进不了核心池**。
   其中 `earnings_revision`（前瞻性预测）与 `northbound_flow`（东财 2024-08
   上游断供，权威口径须 HKEX）按 PIT 铁律**不可认证**。
2. **游资席位专用因子**：数据已可用，因子定义、席位口径选择规则、PIT 审计、
   IC 证据都还没有。
3. **`failed_limit_up`（炸板池）**：仍未找到端点，属「从未接通」。
