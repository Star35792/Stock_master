# 仓库概览 Reference

Stock Master 是 Python 3.13 + FastAPI + DuckDB + SQLAlchemy 的个人量化平台，前端为 Vue3 运维工作台。

## 主要目录

- `backend/app/api/`：HTTP 路由，只调用 service。
- `backend/app/services/`：业务流程编排。
- `backend/app/repositories/`：数据库读写和查询。
- `backend/app/models/`：SQLAlchemy ORM。
- `backend/app/data_sources/`：外部数据源适配，不直接写 `market`。
- `backend/app/lake/`：Parquet/DuckDB 数据湖工具。
- `backend/scripts/`：导入、因子、维护、调度入口。
- `frontend/src/`：Vue3 运维工作台。
- `libs/hithink_sdk/`：独立 HiThink SDK / DuckDB toolkit。
- `aiDoc/`：正式项目文档和 AI 协作 reference 层。

## 不要误用

- `libs/hithink_sdk/marketdb` 不是后端的 DuckDB 可信层。
- `backend/app/lake` 的 curated 数据集是因子服务可配置的生产消费源，不替代 DuckDB 事实层。
- 历史模板、外部参考资料和工具缓存不作为当前实现规范。
