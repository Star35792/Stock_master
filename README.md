# Stock Master

本地 A 股和 ETF 研究工作台。可以查行情、做选股与因子回测、记录手工持仓，并用模拟账户推演组合。真实下单由用户在外部券商终端完成。

## 快速启动

准备 Python 3.13、uv 和 Bun。Bun 版本见 [frontend/.bun-version](frontend/.bun-version)。

1. 在仓库根目录安装后端依赖。首次使用时，把配置模板复制为 `backend/.env`。[配置模板](backend/.env.example)

   ```sh
   uv sync --locked --project backend --extra dev --extra data
   ```

2. 从仓库根目录开一个终端启动后端。请在 `backend/` 下运行，并保持单 worker。

   ```sh
   cd backend
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
   ```

3. 从仓库根目录开另一个终端启动前端。

   ```sh
   cd frontend
   bun install --frozen-lockfile
   bun run dev
   ```

启动后打开[工作台](http://127.0.0.1:5173/overview)或[后端健康检查](http://127.0.0.1:8000/health)。新库需要先导入数据才会显示行情。使用同花顺自动同步时，在 `backend/.env` 配置 `HITHINK_FINANCE_API_KEY`。

## 常用入口

- 查行情、看缺口或排查同步失败。[行情](http://127.0.0.1:5173/market) · [数据质量](http://127.0.0.1:5173/quality) · [运维](http://127.0.0.1:5173/operations)
- 选股、查回测或管理持仓。[研究](http://127.0.0.1:5173/research) · [回测](http://127.0.0.1:5173/backtests) · [持仓](http://127.0.0.1:5173/positions)

## 智能体会话与可审阅计划

在 `backend/.env` 配置 `DEEPSEEK_API_KEY` 并重启后端，就可以在[聊天助手](http://127.0.0.1:5173/agent)里问「概览最近的数据质量问题」或「查通达信 600519.SH 在 2026-11-10 的日线对账」。

在输入框键入 `/` 可以选择数据质量、因子回测或持仓风险技能。想用同一个问题测试另一组假设，在已保存的提问下点击「从此处分支」，改好草稿再发送。分支继承该提问之前的对话，原计划和任务可通过「返回来源对话」查看。较长的对话会提示本轮使用的上下文范围，早期对话仍可查阅。分支接口为 `POST /api/v1/agent/sessions/{session_id}/fork`，传入公开会话里从零开始的 `message_index`。

「采集腾讯 600519.SH 的报价」会生成采集计划。执行需保留本机 `a-stock-data-main/SKILL.md`，设置 `AGENT_DATA_MAINTENANCE_ENABLED=true` 并重启后端。随后批准计划、运行预检并确认该步骤。结果显示采集时间，报价时点和鲜度未核实。[采集与留痕说明](aiDoc/data/source-ingestion-policy.md)

## 文档与开发

[交互式 API 文档](http://127.0.0.1:8000/docs) · [项目文档](aiDoc/README.md) · [路线图](aiDoc/plans/roadmap.md)

从仓库根目录运行 `uv run --project backend --extra dev python scripts/ci_check.py --scope all` 检查后端和前端。

项目代码采用 [AGPL-3.0-only](LICENSE)。第三方组件和数据依各自条款使用。
