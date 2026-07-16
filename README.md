# Stock Master

Stock Master 是一个面向 A 股、ETF 和后续量化研究场景的个人量化平台，按长期维护和扩展来设计。

当前项目优先建设 **可信日线数据中心**：不依赖某一个单一数据源，而是通过多源导入、原始留痕、质量校验和人工修复，形成可供策略与回测使用的本地可信行情库。

## 当前状态

项目目前处于设计和基础骨架阶段。

已有：

- Python/FastAPI 项目骨架。
- PostgreSQL / Redis docker-compose。
- SQLAlchemy / Alembic 依赖配置。
- V1 可信日线数据中心设计文档。

尚未完成：

- CSV/API/QMT 数据导入实现。
- 数据质量校验实现。
- market 可信数据生成流程。

已具备最小查询闭环：

- SQLAlchemy 核心模型和 Alembic 初始迁移。
- `GET /health`。
- `GET /api/v1/securities`。
- `GET /api/v1/daily-bars/{symbol}`。
- `GET /api/v1/data-quality/issues`。

## 技术栈

- Python 3.13
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Redis
- Pandas / Polars（后续）
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
5. **V5 组合、风控和实盘接口**：QMT/MiniQMT、持仓、订单、成交和风控。
6. **V6 可视化平台**：FastAPI + Vue3 + 图表分析和数据质量看板。

## 快速开始

项目使用 uv 管理 Python 和依赖：

```bash
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
```

## 目录结构

```text
app/
├── api/            # HTTP API
├── backtest/       # 回测
├── config/         # 配置
├── database/       # 数据库连接、Session、Base
├── factors/        # 因子计算
├── indicators/     # 技术指标
├── models/         # SQLAlchemy 模型
├── portfolio/      # 组合与持仓
├── repositories/   # 数据访问层
├── risk/           # 风控
├── scheduler/      # 定时任务
├── services/       # 业务服务
├── strategies/     # 策略
└── utils/          # 工具

docs/
├── architecture/   # 架构设计
├── data/           # 数据契约、导入、质量校验
├── database/       # schema 规划
└── roadmap.md      # 路线图
```

## 核心原则

- 任何外部数据源默认不可靠。
- 所有来源先进入 `raw` 原始层。
- 清洗、校验、对账、修复后才能进入 `market` 可信层。
- 因子、策略、回测和 API 查询只消费 `market` 层。
- 人工修复必须记录审计信息。
