# 后端分层规则 Reference

## 依赖方向

```text
api / scripts / scheduler
        ↓
services
        ↓
repositories
        ↓
models / database
```

## 规则

- API 层只处理 HTTP 语义、参数校验和响应，不直接写 SQL。
- Service 层负责业务流程，不处理 HTTP 请求对象。
- Repository 层负责数据库读写和查询，不承载业务决策。
- Model 层只定义 ORM 表结构和关系。
- Alembic 负责 schema 变更。
- DataSource 只负责获取和初步解析外部数据，不直接写 `market`。
- 温层只读仓库（`repositories/research_read.py`）与 ORM 仓库是**孪生实现**：同一份查询语义必须有两条路径，`research_read_source=warm` 走温层、`control` 走 ORM。新增回测/研究读取能力时两条都要改，不能只改一条；两条路径返回的列名、排序和过滤语义必须一致（例如 `list_factor_values` 都按 `trade_date, symbol` 排序）。
- 研究读路径的**写操作仍然只走 ORM**。温层 Parquet 只由离线脚本发布，service 层不得写 Parquet。

## 金融数据规则

- 核心价格、金额、数量、比率使用 `Decimal` / PostgreSQL `numeric`。
- 证券代码统一用 `symbol`。
- 交易日期统一用 `trade_date`。
- 复权类型统一用 `adjust_type`，允许 `none`、`qfq`、`hfq`。
- 策略、回测、组合和模拟盘只消费 `market` / `factor` 可信层。

## 代码变更同步

- 改 API 路由或 schema：更新 `README.md` API 面。
- 改 service 流程：检查 `aiDoc/plans/roadmap.md` 和相关深入设计是否需要同步。
- 改 repository/model/migration：更新 `aiDoc/modules/schema-plan.md`。
- 改因子、策略、回测：更新 `aiDoc/research/` 或 `aiDoc/plans/roadmap.md`。
