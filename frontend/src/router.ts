import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/overview" },
  { path: "/overview", name: "overview" },
  { path: "/market", name: "market" },
  { path: "/factors", name: "factors" },
  { path: "/backtests", name: "backtests" },
  { path: "/portfolio", name: "portfolio" },
  { path: "/paper", name: "paper" },
  { path: "/quality", name: "quality" },
  { path: "/operations", name: "operations" },
  { path: "/alerts", name: "alerts" },
  { path: "/failures", name: "failures" },
  { path: "/:pathMatch(.*)*", redirect: "/overview" },
];

export const router = createRouter({
  history: createWebHistory("/"),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});
