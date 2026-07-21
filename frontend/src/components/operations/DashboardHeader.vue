<script setup lang="ts">
import { Loader2, RefreshCw } from "@lucide/vue";

defineProps<{
  generatedAt: string;
  source: "api" | "demo";
  errorMessage: string | null;
  isLoading: boolean;
  limit: number;
}>();

const emit = defineEmits<{
  refresh: [];
  "update:limit": [value: number];
}>();
</script>

<template>
  <header id="overview" class="topbar">
    <div>
      <p class="eyebrow">V6 Operations Console</p>
      <h1>运维工作台</h1>
    </div>

    <div class="toolbar">
      <div class="segmented" aria-label="记录数量">
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
    <span :class="['source-pill', source]">{{ source === "api" ? "Live API" : "Demo Data" }}</span>
    <span>生成时间 {{ generatedAt }}</span>
    <span class="readonly-pill">只读诊断</span>
    <span v-if="errorMessage" class="inline-error">{{ errorMessage }}</span>
  </section>
</template>
