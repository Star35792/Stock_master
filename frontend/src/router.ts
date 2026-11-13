import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import WorkspaceRoutePage from "./pages/WorkspaceRoutePage.vue";
import BacktestRunPage from "./pages/BacktestRunPage.vue";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/overview" },
  ...["overview", "market", "factors", "price-value", "backtests", "positions", "portfolio", "paper", "quality", "operations", "alerts", "failures"].map(
    (name): RouteRecordRaw => ({ path: `/${name}`, name, component: WorkspaceRoutePage, props: { page: name } }),
  ),
  { path: "/backtests/runs/:runId", name: "backtest-run", component: BacktestRunPage },
  { path: "/:pathMatch(.*)*", redirect: "/overview" },
];

export const router = createRouter({
  history: createWebHistory("/"),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});
