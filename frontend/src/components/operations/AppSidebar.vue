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

export type NavGroup = {
  label: string;
  items: Array<{
    id: string;
    label: string;
    icon: Component;
  }>;
};

defineProps<{
  groups: NavGroup[];
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
      <section v-for="group in groups" :key="group.label" class="nav-group">
        <span class="nav-group-label">{{ group.label }}</span>
        <RouterLink
          v-for="item in group.items"
          :key="item.id"
          :to="{ name: item.id }"
          class="nav-link"
          active-class="is-active"
        >
          <component :is="item.icon" :size="18" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </section>
    </nav>
  </aside>
</template>
