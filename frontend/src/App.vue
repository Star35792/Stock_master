<script setup lang="ts">
import {
  Activity,
  Bot,
  BarChart3,
  BriefcaseBusiness,
  ClipboardList,
  FlaskConical,
  LayoutDashboard,
  LineChart,
  Scale,
  ServerCog,
  ShieldCheck,
  Wallet,
} from "@lucide/vue";

import AppSidebar from "./components/operations/AppSidebar.vue";

const navGroups = [
  {
    label: "研究主线",
    items: [
      { id: "agent", label: "智能体", icon: Bot },
      { id: "research", label: "研究首页", icon: LayoutDashboard },
      { id: "factors", label: "因子", icon: BarChart3 },
      { id: "price-value", label: "估值研究", icon: Scale },
      { id: "backtests", label: "回测", icon: FlaskConical },
      { id: "portfolio", label: "组合草案", icon: ClipboardList },
      { id: "positions", label: "持仓分析", icon: Wallet },
      { id: "paper", label: "模拟盘", icon: BriefcaseBusiness },
    ],
  },
  {
    label: "市场与数据",
    items: [
      { id: "market", label: "行情", icon: LineChart },
      { id: "quality", label: "质量", icon: ShieldCheck },
    ],
  },
  {
    label: "运维与监控",
    items: [
      { id: "overview", label: "总览", icon: Activity },
      { id: "operations", label: "运维", icon: ServerCog },
    ],
  },
];
</script>

<template>
  <div class="app-shell">
    <AppSidebar :groups="navGroups" />

    <main class="workspace">
      <RouterView />
    </main>
  </div>
</template>
