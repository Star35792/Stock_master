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

- Vue 3 + TypeScript 单页应用；路由在 `src/router.ts`，页面在 `src/pages/`（Market、Factor、BacktestRun、Positions、PaperTrading、PriceValue、WorkspaceRoute），运维子组件在 `src/components/operations/`。
- 后端调用集中在 `src/api.ts` 与 `src/factorSelectionApi.ts`、`src/priceValueApi.ts`、`src/priceValueBacktestApi.ts`、`src/priceValueScreenApi.ts`；接口前缀 `/api/v1`。
- `vite.config.ts` 将 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`，可用 `STOCK_MASTER_API_URL` 覆盖。
- `dist/` 与 `node_modules/` 已在根 `.gitignore` 中，是可再生产物，不入库。

## 验证选择

- 类型、构建或数据展示逻辑变化，运行相关测试和 `bun run build`；路由、表单、交互或视觉变化还须在实际页面走一次受影响流程，检查可见结果与错误状态。验证范围由变更决定，不以构建通过代替用户流程验收。

## 前端专属坑

- `vue-tsc` 必须 ≥ 3.3.14：3.3.7 在 Bun 运行时下对 `./App.vue` 报 3 次 TS2307，而同一 `node_modules` 用真实 node 跑、或单独 `bun x vite build` 都正常——是 vue-tsc/Bun 兼容缺陷，不是锁文件或依赖问题。
- 不要把 `test` 脚本写回 `node --test`：CI 与本地都不再保证有 node，且 Bun 执行该脚本会报 "Cannot use test outside of the test runner"。
