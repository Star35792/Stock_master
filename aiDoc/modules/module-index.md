# 模块索引 Reference

## 后端模块

| 模块 | 当前职责 |
| --- | --- |
| `api` | HTTP 语义、参数校验、响应模型 |
| `services` | 导入、构建、因子、回测、组合、风控、模拟盘等业务流程 |
| `repositories` | SQLAlchemy 查询、upsert、分页、持久化 |
| `models` | ORM 表结构 |
| `data_sources` | HiThink、Eastmoney、Xueqiu 等外部来源适配 |
| `scheduler` | 工作日同步和近期补齐调度 |
| `lake` | Parquet/DuckDB 离线数据湖 |
| `backtest` | 日频事件回测内核 |
| `strategies` | 策略研究模板 |
| `portfolio` | 组合构建内核 |
| `risk` | 下单前风控内核 |
| `trade` | BrokerAdapter 和模拟盘执行 |

当前公共边界：`api` 已暴露行情、数据质量、因子、回测、策略研究和运维查询；组合、风控、模拟盘服务尚未暴露公共操作路由或请求/响应 schema。`trade` 当前只有纸盘 BrokerAdapter，不能视为真实账户只读或受控交易适配器。

## Schema 域

- `raw`：导入批次、原始行情、原始财务、原始公司行为、vendor snapshot。
- `market`：证券、交易日历、可信日线、复权因子、公司行为、另类数据、估值快照。
- `fundamental`：财务报表和指标。
- `factor`：因子定义、运行、值、profile。
- `strategy`：回测运行、成交、净值、持仓、绩效。
- `portfolio`：组合计划和目标。
- `risk`：风控检查。
- `trade`：模拟盘账户、订单、成交、持仓。
- `system`：任务、质量问题、数据源配置。

## 同步规则

- 新增/改动 schema 域：更新 `aiDoc/modules/schema-plan.md`。
- 新增 API：更新 `README.md` 核心 API 面。
- 新增模块或改变职责：更新本文件和 `AGENT.MD` 仓库概览。
