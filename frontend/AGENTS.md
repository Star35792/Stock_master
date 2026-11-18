# frontend · 模块规则

作用域：`frontend/**`。跨模块规则见根 [`AGENTS.md`](../AGENTS.md)。

## 运行时与命令（工作目录 `frontend/`）

- 唯一运行时是 **Bun**，版本以 `.bun-version` 为准。不使用 npm / pnpm / yarn：`scripts/ci_check.py` 的前端步骤只认 PATH 上的 `bun`，缺失即退出 127 且不做包管理器回退（有契约测试锁定）。
- 安装：`bun install --frozen-lockfile`（锁文件 `bun.lock` 已入库，`.gitattributes` 强制 LF）。
- 测试：`bun test`；测试范围以 `tests/` 中的当前文件为准。
- 构建：`bun run build` = `vue-tsc --noEmit && vite build`。
- 本地全门（与 CI 同一入口）：`uv run --project ../backend python ../scripts/ci_check.py --scope frontend`（需 uv 与 Bun 同在 PATH；CI 用 `oven-sh/setup-bun@v2` + `bun-version-file: frontend/.bun-version`）。
- 开发服务器：`bun run dev`（Vite，绑定 127.0.0.1）。

## 结构事实

- Vue 3 + TypeScript 单页应用；路由在 `src/router.ts`，每条路由 1:1 指向独立页面组件并全部懒加载：`src/pages/`（Overview、Research、Operations、Quality、Backtests、Market、Factor、BacktestRun、Positions、PaperTrading、PriceValue），组合草案页直接挂 `src/components/operations/PortfolioPlanSection.vue`；`/alerts` 重定向到 `/overview`，`/failures` 重定向到 `/operations`。
- 运维状态（总览/告警/失败记录/失败详情与重跑计划）集中在 `src/composables/operationsDashboard.ts`：`createOperationsDashboard(deps)` 支持依赖注入（测试用），页面经 `useOperationsDashboard()` 共享单例；仅运维域页面（总览、运维、质量、回测列表）挂载时触发加载，研究主线页面不请求运维接口。`App.vue` 只保留侧边栏与 `RouterView`。
- 复杂页面的状态逻辑抽到 `src/composables/`（`researchWorkbench.ts`、`factorWorkspace.ts`、`positionBook.ts`、`portfolioPlan.ts`），均为「工厂函数 + 依赖注入 + 视图层只做接线」的形态；测试经 `tests/workflow.test.mjs` 的 `setupModule()` 直接编译这些 composable 验证契约，SFC 自身只保留模板与接线。因子中文标签等纯函数在 `src/components/operations/factorLabels.ts`。
- 后端调用集中在 `src/api.ts` 与 `src/researchWorkflowApi.ts`、`src/factorSelectionApi.ts`、`src/priceValueApi.ts`、`src/priceValueBacktestApi.ts`、`src/priceValueScreenApi.ts`；接口前缀 `/api/v1`。
- `vite.config.ts` 将 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`，可用 `STOCK_MASTER_API_URL` 覆盖。
- `dist/` 与 `node_modules/` 已在根 `.gitignore` 中，是可再生产物，不入库。

## 验证选择

- 类型、构建或数据展示逻辑变化，运行相关测试和 `bun run build`；路由、表单、交互或视觉变化还须在实际页面走一次受影响流程，检查可见结果与错误状态。验证范围由变更决定，不以构建通过代替用户流程验收。

## 前端专属坑

- `vue-tsc` 必须 ≥ 3.3.14：3.3.7 在 Bun 运行时下对 `./App.vue` 报 3 次 TS2307，而同一 `node_modules` 用真实 node 跑、或单独 `bun x vite build` 都正常——是 vue-tsc/Bun 兼容缺陷，不是锁文件或依赖问题。
- 不要把 `test` 脚本写回 `node --test`：CI 与本地都不再保证有 node，且 Bun 执行该脚本会报 "Cannot use test outside of the test runner"。
