<script setup lang="ts">
import { DatabaseZap } from "@lucide/vue";
import { nextTick, ref, watch, type Component } from "vue";
import { RouterLink, useRoute } from "vue-router";

const route = useRoute();
const navList = ref<HTMLElement | null>(null);

watch(
  () => route.name,
  async () => {
    await nextTick();
    navList.value?.querySelector<HTMLElement>(".nav-link.is-active")?.scrollIntoView({
      block: "nearest",
      inline: "center",
    });
  },
  { immediate: true },
);

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

    <nav ref="navList" class="nav-list" aria-label="量化工作台页面">
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
