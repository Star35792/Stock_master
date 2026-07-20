# Stock Master

Stock Master 是一个面向 A 股、ETF 和后续量化研究场景的个人量化平台，按长期维护和扩展来设计。

当前项目已经从 **可信日线数据中心** 推进到因子、回测、组合、风控、模拟盘和运维观测的最小闭环。不依赖某一个单一数据源，而是通过多源导入、原始留痕、质量校验和人工修复，形成可供策略、回测和组合执行使用的本地可信数据底座。

## 当前状态

项目当前处于 **V6 可视化和运维** 的最小后端观测阶段，下一项推荐进入 **V7 实盘只读与受控交易**。

已有：

- Python/FastAPI/PostgreSQL/SQLAlchemy/Alembic 基础工程。
- `raw -> market -> factor -> research/backtest -> portfolio/risk -> trade` 分层闭环。
- 参考数据同步、真实采集、`raw -> market` 构建统一入口。
- 基础价量因子、横截面处理、因子统计报告。
- 回测、绩效归因、组合构建、下单前风控。
- 模拟盘订单、成交、现金和持仓闭环。
- 运维概览接口，聚合数据质量、任务、因子、回测和模拟盘状态。

常用 API：

- `GET /health`
- `GET /api/v1/securities`
- `GET /api/v1/daily-bars/{symbol}`
- `GET /api/v1/daily-bars/missing`
- `GET /api/v1/data-quality/issues`
- `GET /api/v1/factors/definitions`
- `GET /api/v1/factors/runs`
- `GET /api/v1/factors/values`
- `GET /api/v1/factors/profiles`
- `GET /api/v1/operations/overview`

## 技术栈

- Python 3.13
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Redis
- Pandas / Polars
- Backtrader
- Plotly
- Vue3（后续）

## 文档入口

正式设计文档见：

- [docs/README.md](docs/README.md)
- [V1 可信日线数据中心架构](docs/architecture/trusted-daily-data-center.md)
- [日线数据字段契约](docs/data/daily-bar-contract.md)
- [数据库 Schema 规划](docs/database/schema-plan.md)
- [路线图](docs/roadmap.md)

## 阶段规划

1. **V1 可信日线数据中心**：多源导入 + PostgreSQL + 原始留痕 + 质量校验 + 人工修复。
2. **V2 复权、ETF 和指数增强**：复权因子、交易日历、ETF/指数扩展。
3. **V3 因子工厂**：技术面、量价、趋势、资金面等因子沉淀。
4. **V4 策略与回测**：多因子评分、ETF 轮动、股票选股、绩效分析。
5. **V5 组合、风控和模拟盘**：组合构建、下单前风控、模拟订单、成交、现金和持仓。
6. **V6 可视化和运维**：运维概览接口、数据质量、任务、因子、回测和模拟盘状态。
7. **V7 实盘只读与受控交易**：真实账户只读同步、人工确认、风控边界和应急开关。

## 快速开始

项目使用 uv 管理 Python 和依赖：

```bash
cd backend
cp .env.example .env
docker compose up -d
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

常用验证命令：

```bash
uv run --extra dev ruff check app tests alembic
uv run --extra dev pytest
uv run alembic current
```

## 目录结构

```text
backend/
├── app/            # Python/FastAPI 后端应用代码
├── alembic/        # 数据库迁移
├── data/           # 本地数据和数据湖文件
├── scripts/        # 后端导入、因子、维护和调度入口
├── sql/            # SQL 资源
├── tests/          # 后端测试
├── pyproject.toml  # Python 项目配置
└── docker-compose.yml

docs/
├── architecture/   # 架构设计
├── data/           # 数据契约、导入、质量校验
├── database/       # schema 规划
└── roadmap.md      # 路线图

frontend/
└── src/            # Vue3 前端工作台
```

## 核心原则

- 任何外部数据源默认不可靠。
- 所有来源先进入 `raw` 原始层。
- 数据获取本身可能不完整：单一数据源可能缺新 ETF、指数或特殊品种，允许引入补充数据源，但必须保留来源、批次和原始载荷。
- 清洗、校验、对账、修复后才能进入 `market` 可信层。
- 因子、策略、回测和 API 查询只消费 `market` / `factor` 可信层。
- 人工修复必须记录审计信息。
- 实盘写操作必须有人工确认、风控边界和可追溯审计；当前默认只推进模拟盘和只读边界。
