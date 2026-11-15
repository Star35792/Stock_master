# 仓库概览 Reference

Stock Master 是 Python 3.13 + FastAPI + SQLAlchemy 2.x + DuckDB 的个人量化平台（只做选股研究与持仓管理，不接券商、不发真实订单），前端为 Vue 3 + TypeScript + Vite + Bun 工作台。跨模块 AI 协作规则真源是根 `AGENTS.md`，模块命令与边界写在各目录自己的 `AGENTS.md`。

## 主要目录

- `backend/app/api/`：HTTP 路由（16 个业务路由文件 + 根路径 `health`，统一前缀 `/api/v1`，共 77 个端点：50 读 / 27 写），只调用 service。
- `backend/app/schemas/`：请求/响应 Pydantic 合同（含费用模型版本白名单等约束）。
- `backend/app/services/`：业务流程编排，含 `job_lease.py` 的任务 OS 租约与孤儿收敛。
- `backend/app/repositories/`：数据库读写与查询，upsert 走 `ON CONFLICT DO UPDATE`。
- `backend/app/models/`：SQLAlchemy ORM（50 个 `__tablename__`，分布在三个库）。
- `backend/app/database/`：三库 engine/session 与初始化兼容桥（`initialize_database` / `apply_schema_compatibility` / `checkpoint_duckdb`）。
- `backend/app/config/`：`settings.py`（pydantic-settings，读 `backend/.env`）与 `data_contracts.py`（合同版本常量，如复权因子可信来源）。
- `backend/app/scheduler/`：随 API 进程启动的 3 个后台 worker（自动同步、估值输入准备、估值初筛）与 APScheduler 工厂；多数 cron 工厂无仓内调用方，须显式用 `backend/scripts/scheduler/` 运行。
- `backend/app/data_sources/`：12 个数据源适配器模块 + `base.py`，只产 `raw`，不直接写 `market`。HiThink 走本目录自有 urllib 实现，不 import `libs/hithink_sdk`。
- `backend/app/lake/`、`backend/app/market/`：温层 release 读写与可信层构建。
- `backend/app/factors/`、`indicators/`、`strategies/`、`backtest/`（自研日频事件引擎，合同 `daily_event_v4`）、`portfolio/`、`risk/`、`trade/`（本地模拟推演）：研究到持仓推演的能力层。
- `backend/app/utils/`：跨层工具，含 `duckdb_write_lock.py`（域写锁）与 `discard.py`。
- `backend/scripts/`：导入、因子、维护、修复、调度与研究脚本入口（一次性任务，写库前持域锁）。
- `backend/tests/`：pytest 用例，`test_ci_check_runner.py` 锁定共享检查门契约。
- `frontend/`：Vue 3 工作台；12 个工作区路由由 `WorkspaceRoutePage.vue` 分发，回测详情独立页；`bun test` + `bun run build`。
- `libs/a_stock_data/`：本地代码，把 `a-stock-data` 的 SKILL.md 内嵌 Python 净化为可 import 模块，被 `data_sources/tencent.py`、`tdx_official.py` 真实 import；导入时不得发起网络请求。
- `libs/hithink_sdk/`：上游 HiThink MarketDB SDK 副本（包名 `marketdb`），后端不 import，不参与检查门。
- `scripts/`：仓库级共享脚本，`ci_check.py` 是本地与 CI 共用的唯一门。
- `deliverables/`：入库的研究产物与证据（选股池 CSV、回测意图清单、价值初筛快照）。
- `aiDoc/`：正式项目文档和 AI 协作 reference 层。

## 存储与初始化

三库与温层都在 `backend/data/`：主库 `stock_master.duckdb`（控制面 + 事务事实 + 温层 release 链，41 表）、研究库 `research.duckdb`（strategy 回测产物，7 表）、持仓账本 `positions.db`（SQLite，2 表），大体量列式历史在 `warm/` Parquet。职责边界见 [系统关系](system-map.md)。各库初始化入口位于 `backend/app/database/`，由 `app/main.py` 的 lifespan 依次调用；当前没有 Alembic 迁移目录，完整版本化迁移与回退体系仍是缺口。

数据路径按当前工作目录解析，因此写锁文件也在 `backend/data/`：从仓库根运行研究脚本会落到 `data/research_write.lock.guard`，与 `backend/data/` 的同名锁不是同一把锁。任务统一从 `backend/` 目录启动。

## 不要误用

- `libs/hithink_sdk/marketdb` 不是后端的 DuckDB 可信层，也不是后端依赖。
- `backend/app/lake` 的 curated 数据集是因子服务可配置的生产消费源，不替代 DuckDB 事实层。
- `OSkhQuant-main/`、`a-stock-data-main/`、`同花顺数据来源参考/` 是未入库的外部参考，不是实现规范。
- `backtrader` 与 `plotly` 仍在 `pyproject.toml` 的可选组里，但仓内已无 import；回测是仓库自研引擎。
- 历史模板、外部参考资料和工具缓存不作为当前实现规范。
