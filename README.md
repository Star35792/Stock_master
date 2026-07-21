# Stock Master

Stock Master 是一个面向 A 股、ETF 和后续量化研究场景的个人量化平台，按长期维护和扩展来设计。

当前项目已经从 **可信日线数据中心** 推进到因子、回测、组合、风控、模拟盘和运维观测的最小闭环。V4 单策略研究合同与可复现回测切片已落地，运行记录可还原完整输入和版本。不依赖某一个单一数据源，而是通过多源导入、原始留痕、质量校验和人工修复，形成可供策略、回测和组合执行使用的本地可信数据底座。

V4 研究接口支持批量运行、样本外/滚动验证合同、按指定交易所开市日生成窗口、基准与跨运行比较。回归漂移诊断只比较研究合同、测试区间和数据版本一致的运行，阈值表示收益率与回撤率的绝对差值；训练和选参仍由调用方完成并负责留痕。

## 当前状态

项目当前处于 **V6 可视化和运维** 的最小工作台阶段。已具备运维概览、告警候选摘要、按分类限量的失败定位摘要、单项失败详情和只读重跑计划；下一步深化细粒度重跑执行入口，再补 V5 组合/风控/模拟盘公共操作闭环。V4 保留训练选参编排与审计缺口，V7 实盘只读与受控交易尚未开始。

已有：

- Python/FastAPI/PostgreSQL/SQLAlchemy/Alembic 基础工程。
- `raw -> market -> factor -> research/backtest -> portfolio/risk -> trade` 分层闭环。
- 参考数据同步、真实采集、`raw -> market` 构建统一入口。
- HiThink、东方财富补充源、CSV、数据湖和近期缺口补采的导入/维护入口。
- 基础价量、财务、估值、行业、资金流、拥挤度、情绪、宏观、政策、盈利预期和复合因子首版。
- 横截面处理、IC/Rank IC、forward diagnostics、top quantile turnover 等因子统计报告。
- 回测、绩效归因、因子选股研究入口、组合构建、下单前风控。
- 模拟盘订单、成交、现金和持仓闭环。
- 运维概览接口与 Vue3 运维工作台，聚合数据质量、任务、因子、回测和模拟盘状态。

核心 API 面：

- 健康检查：`GET /health`
- 行情与参考数据：`GET /api/v1/securities`、`GET /api/v1/securities/{symbol}/lifecycle`、`GET /api/v1/daily-bars/{symbol}`、`GET /api/v1/daily-bars/missing`、`GET /api/v1/trading-calendar`
- 数据质量与修复：`GET /api/v1/data-quality/issues`、`PATCH /api/v1/data-quality/issues/{issue_id}/status`、`GET /api/v1/daily-bar-source-compares`、`GET /api/v1/manual-corrections`
- 复权与公司行为：`GET /api/v1/corporate-actions`、`GET /api/v1/adjustment-factors`
- 因子：`GET /api/v1/factors/definitions`、`GET /api/v1/factors/runs`、`GET /api/v1/factors/values`、`GET /api/v1/factors/profiles`
- 研究与回测：`POST /api/v1/strategy-research/rolling-windows`、`POST /api/v1/strategy-research/factor-selection/backtests`、`POST /api/v1/strategy-research/factor-selection/backtests/batch`、`GET /api/v1/backtests/runs`、`GET /api/v1/backtests/runs/{run_id}`、`GET /api/v1/backtests/runs/{run_id}/trades`、`GET /api/v1/backtests/runs/{run_id}/equity`、`GET /api/v1/backtests/runs/{run_id}/positions`、`GET /api/v1/backtests/runs/{run_id}/performance`、`POST /api/v1/backtests/runs/{run_id}/performance`、`GET /api/v1/backtests/runs/{run_id}/benchmark`、`POST /api/v1/backtests/runs/compare`、`POST /api/v1/backtests/runs/drift`
- 运维工作台：`GET /api/v1/operations/overview`、`GET /api/v1/operations/alerts`、`GET /api/v1/operations/failures`、`GET /api/v1/operations/failures/{failure_type}/{failure_id}`、`GET /api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan`

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
- Vue3

## 文档入口

项目文档入口见：

- [AI 协作参考入口](aiDoc/README.md)
- [V1 可信日线数据中心架构](aiDoc/relations/trusted-daily-data-center.md)
- [日线数据字段契约](aiDoc/data/daily-bar-contract.md)
- [数据库 Schema 规划](aiDoc/modules/schema-plan.md)
- [路线图](aiDoc/plans/roadmap.md)

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

aiDoc/
├── data/           # 数据契约、导入、质量校验
├── modules/        # 模块索引、后端规则、schema 规划
├── plans/          # 路线图和阶段计划
├── relations/      # 仓库概览、系统关系、架构设计
├── research/       # 因子、策略和研究方法设计
└── memory/         # 跨任务稳定记忆

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
