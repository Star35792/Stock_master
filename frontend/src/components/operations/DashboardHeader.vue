<script setup lang="ts">
import { Loader2, RefreshCw } from "@lucide/vue";
import { computed } from "vue";

const props = defineProps<{
  page: string;
  generatedAt: string;
  source: "loading" | "api" | "partial" | "demo";
  errorMessage: string | null;
  isLoading: boolean;
  limit: number;
}>();

const pageCopy = computed(() => ({
  overview: { eyebrow: "STOCK MASTER / OVERVIEW", title: "量化工作台总览", scope: "只读概览" },
  quality: { eyebrow: "DATA QUALITY", title: "数据质量", scope: "只读诊断" },
  backtests: { eyebrow: "RESEARCH RUNS", title: "回测与运行记录", scope: "只读研究" },
  portfolio: { eyebrow: "PORTFOLIO DRAFT", title: "组合草案", scope: "模拟草案" },
  operations: { eyebrow: "OPERATIONS", title: "运维中心", scope: "只读诊断" },
  alerts: { eyebrow: "ALERTS", title: "告警中心", scope: "只读诊断" },
  failures: { eyebrow: "FAILURES", title: "失败中心", scope: "只读诊断" },
} as Record<string, { eyebrow: string; title: string; scope: string }>)[props.page] ?? { eyebrow: "STOCK MASTER", title: "量化工作台", scope: "只读" });

const showLimit = computed(() => ["overview", "quality", "backtests", "operations", "failures"].includes(props.page));

const emit = defineEmits<{
  refresh: [];
  "update:limit": [value: number];
}>();
</script>

<template>
  <header id="overview" class="topbar">
    <div>
      <p class="eyebrow">{{ pageCopy.eyebrow }}</p>
      <h1>{{ pageCopy.title }}</h1>
    </div>

    <div class="toolbar">
      <div v-if="showLimit" class="segmented" aria-label="记录数量">
        <button :class="{ active: limit === 5 }" type="button" @click="emit('update:limit', 5)">5</button>
        <button :class="{ active: limit === 10 }" type="button" @click="emit('update:limit', 10)">10</button>
        <button :class="{ active: limit === 20 }" type="button" @click="emit('update:limit', 20)">20</button>
      </div>
      <button class="icon-button" type="button" title="刷新" @click="emit('refresh')">
        <Loader2 v-if="isLoading" class="spin" :size="18" aria-hidden="true" />
        <RefreshCw v-else :size="18" aria-hidden="true" />
      </button>
    </div>
  </header>

  <section class="status-strip" aria-label="Connection status">
    <span :class="['source-pill', source]">{{ source === "loading" ? "正在加载" : source === "api" ? "API 已连接" : source === "partial" ? "部分数据不可用" : "演示数据" }}</span>
    <span>生成时间 {{ generatedAt }}</span>
    <span class="readonly-pill">{{ pageCopy.scope }}</span>
    <span v-if="errorMessage" class="inline-error">{{ errorMessage }}</span>
  </section>

  <section v-if="source === 'demo'" class="demo-banner" role="alert">
    后端运维接口不可用，当前页面展示的是内置演示数据，不代表真实运行状态；数据恢复前请勿据此做研究或持仓决策。
  </section>
</template>
