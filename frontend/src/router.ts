import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  { path: "/agent", name: "agent", component: () => import("./pages/AgentPage.vue") },
  { path: "/", redirect: "/overview" },
  { path: "/overview", name: "overview", component: () => import("./pages/OverviewPage.vue") },
  { path: "/research", name: "research", component: () => import("./pages/ResearchPage.vue") },
  { path: "/market", name: "market", component: () => import("./pages/MarketPage.vue") },
  { path: "/factors", name: "factors", component: () => import("./pages/FactorPage.vue") },
  { path: "/price-value", name: "price-value", component: () => import("./pages/PriceValuePage.vue") },
  { path: "/backtests", name: "backtests", component: () => import("./pages/BacktestsPage.vue") },
  { path: "/positions", name: "positions", component: () => import("./pages/PositionsPage.vue") },
  { path: "/portfolio", name: "portfolio", component: () => import("./components/operations/PortfolioPlanSection.vue") },
  { path: "/paper", name: "paper", component: () => import("./pages/PaperTradingPage.vue") },
  { path: "/quality", name: "quality", component: () => import("./pages/QualityPage.vue") },
  { path: "/operations", name: "operations", component: () => import("./pages/OperationsPage.vue") },
  { path: "/alerts", redirect: "/overview" },
  { path: "/failures", redirect: "/operations" },
  { path: "/backtests/runs/:runId", name: "backtest-run", component: () => import("./pages/BacktestRunPage.vue") },
  { path: "/:pathMatch(.*)*", redirect: "/overview" },
];

export const router = createRouter({
  history: createWebHistory("/"),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});
