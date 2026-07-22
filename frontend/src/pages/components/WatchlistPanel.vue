<script setup lang="ts">
import { Star, X } from "@lucide/vue";

defineProps<{ symbols: string[]; selectedSymbol: string }>();

const emit = defineEmits<{
  (event: "select", symbol: string): void;
  (event: "remove", symbol: string): void;
}>();
</script>

<template>
  <section class="watchlist-panel market-panel">
    <header class="watchlist-panel__header">
      <div>
        <span class="market-kicker">本地偏好</span>
        <h2>自选股</h2>
      </div>
      <Star :size="18" />
    </header>
    <div v-if="!symbols.length" class="market-empty market-empty--compact">
      还没有自选证券。搜索后点击星标即可加入，本列表只保存在当前浏览器。
    </div>
    <ul v-else class="watchlist-list">
      <li v-for="symbol in symbols" :key="symbol">
        <button type="button" :class="['watchlist-symbol', { active: symbol === selectedSymbol }]" @click="emit('select', symbol)">
          {{ symbol }}
        </button>
        <button type="button" class="watchlist-remove" :aria-label="`移除 ${symbol}`" @click="emit('remove', symbol)">
          <X :size="14" />
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.watchlist-panel { display: grid; gap: 14px; }
.watchlist-panel__header { display: flex; justify-content: space-between; color: #d97706; }
.watchlist-panel__header > div { display: grid; gap: 5px; }
.watchlist-panel h2 { color: #101828; font-size: 18px; }
.market-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.market-empty { padding: 18px; color: #667085; background: #f8fafc; font-size: 12px; line-height: 1.5; }
.market-empty--compact { padding: 12px; }
.watchlist-list { display: grid; max-height: 240px; gap: 5px; overflow-y: auto; margin: 0; padding: 0; list-style: none; }
.watchlist-list li { display: flex; align-items: center; gap: 4px; }
.watchlist-symbol { flex: 1; min-height: 34px; border: 1px solid transparent; border-radius: 6px; color: #344054; text-align: left; background: transparent; cursor: pointer; font-weight: 700; }
.watchlist-symbol:hover, .watchlist-symbol.active { border-color: #dbeafe; color: #1d4ed8; background: #eff6ff; }
.watchlist-remove { display: grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 6px; color: #98a2b3; background: transparent; cursor: pointer; }
.watchlist-remove:hover { color: #b42318; background: #fef3f2; }
</style>
