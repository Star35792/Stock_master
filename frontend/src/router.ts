import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import WorkspaceRoutePage from "./pages/WorkspaceRoutePage.vue";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/overview" },
  ...["overview", "market", "factors", "backtests", "portfolio", "paper", "quality", "operations", "alerts", "failures"].map(
    (name): RouteRecordRaw => ({ path: `/${name}`, name, component: WorkspaceRoutePage, props: { page: name } }),
  ),
  { path: "/:pathMatch(.*)*", redirect: "/overview" },
];

export const router = createRouter({
  history: createWebHistory("/"),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});
