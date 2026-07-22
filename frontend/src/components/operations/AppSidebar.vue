<script setup lang="ts">
import { DatabaseZap } from "@lucide/vue";
import type { Component } from "vue";
import { RouterLink } from "vue-router";

defineProps<{
  navItems: Array<{
    id: string;
    label: string;
    icon: Component;
  }>;
}>();
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <DatabaseZap :size="28" aria-hidden="true" />
      <div>
        <strong>Stock Master</strong>
        <span>Quant Ops</span>
      </div>
    </div>

    <nav class="nav-list" aria-label="Dashboard sections">
      <RouterLink
        v-for="item in navItems"
        :key="item.id"
        :to="{ name: item.id }"
        class="nav-link"
        active-class="is-active"
      >
        <component :is="item.icon" :size="18" aria-hidden="true" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
  </aside>
</template>
