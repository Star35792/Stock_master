<script setup lang="ts">
import type { Component } from "vue";

import { formatNumber } from "./formatters";

defineProps<{
  cards: Array<{
    label: string;
    value: number | null;
    detail: string;
    tone: string;
    icon: Component;
  }>;
}>();
</script>

<template>
  <section class="metric-grid" aria-label="Key metrics">
    <article v-for="card in cards" :key="card.label" :class="['metric-card', card.tone]">
      <component :is="card.icon" :size="22" aria-hidden="true" />
      <div>
        <span>{{ card.label }}</span>
        <strong>{{ formatNumber(card.value) }}</strong>
        <small>{{ card.detail }}</small>
      </div>
    </article>
  </section>
</template>
