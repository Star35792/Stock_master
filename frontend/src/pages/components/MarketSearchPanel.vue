<script setup lang="ts">
import { Search, Star } from "@lucide/vue";

export interface SecurityItem {
  symbol: string;
  name: string;
  security_type: string;
  exchange: string;
  industry: string | null;
  is_active: boolean;
}

defineProps<{
  keyword: string;
  items: SecurityItem[];
  loading: boolean;
  selectedSymbol: string;
  watchlist: string[];
}>();

const emit = defineEmits<{
  (event: "update:keyword", value: string): void;
  (event: "search"): void;
  (event: "select", item: SecurityItem): void;
  (event: "toggle-watchlist", symbol: string): void;
}>();
</script>

<template>
  <section class="market-panel market-search-panel">
    <header class="market-panel__header">
      <div>
        <span class="market-kicker">证券检索</span>
        <h2>搜索证券</h2>
        <p>按代码或名称查找股票、ETF 和指数。</p>
      </div>
      <Search :size="19" aria-hidden="true" />
    </header>
    <form class="market-search-form" @submit.prevent="emit('search')">
      <label for="market-search-input">代码或名称</label>
      <div class="market-search-form__row">
        <input
          id="market-search-input"
          :value="keyword"
          placeholder="例如 600519 或 贵州茅台"
          autocomplete="off"
          @input="emit('update:keyword', ($event.target as HTMLInputElement).value)"
        />
        <button type="submit" class="market-primary-button" :disabled="loading">
          {{ loading ? "搜索中" : "搜索" }}
        </button>
      </div>
    </form>
    <div v-if="!loading && !items.length" class="market-empty market-empty--compact">
      没有匹配证券，请尝试完整代码或中文名称。
    </div>
    <ul v-else class="market-security-list">
      <li v-for="item in items" :key="item.symbol">
        <div :class="['market-security-item', { 'market-security-item--active': selectedSymbol === item.symbol }]">
          <button type="button" class="market-security-select" @click="emit('select', item)">
            <span>
            <strong>{{ item.symbol }}</strong>
            <small>{{ item.name }} · {{ item.exchange }}</small>
            </span>
          </button>
          <span class="market-security-item__meta">
            <small>{{ item.security_type.toUpperCase() }}</small>
            <button
              type="button"
              class="market-icon-button"
              :aria-label="watchlist.includes(item.symbol) ? '移出自选' : '加入自选'"
              @click.stop="emit('toggle-watchlist', item.symbol)"
            >
              <Star :size="15" :fill="watchlist.includes(item.symbol) ? 'currentColor' : 'none'" />
            </button>
          </span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.market-search-panel { display: grid; gap: 18px; }
.market-panel__header { display: flex; justify-content: space-between; gap: 12px; color: #2563eb; }
.market-panel__header > div { display: grid; gap: 5px; }
.market-panel__header h2 { color: #101828; font-size: 18px; }
.market-panel__header p { color: #667085; font-size: 12px; }
.market-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.market-search-form { display: grid; gap: 7px; }
.market-search-form label { color: #667085; font-size: 12px; font-weight: 700; }
.market-search-form__row { display: flex; gap: 8px; }
.market-search-form input { min-width: 0; flex: 1; min-height: 38px; border: 1px solid #d7dce4; border-radius: 7px; padding: 0 10px; font: inherit; }
.market-primary-button { min-height: 38px; border: 1px solid #2563eb; border-radius: 7px; padding: 0 14px; color: #fff; background: #2563eb; cursor: pointer; font-weight: 700; }
.market-primary-button:disabled { opacity: .55; cursor: wait; }
.market-empty { padding: 18px; color: #667085; background: #f8fafc; font-size: 12px; line-height: 1.5; }
.market-empty--compact { padding: 12px; }
.market-security-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.market-security-item { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 10px; border: 1px solid #eef1f5; border-radius: 7px; padding: 6px 8px 6px 10px; color: #344054; text-align: left; background: #fbfcfe; }
.market-security-item:hover, .market-security-item--active { border-color: #9db7f5; background: #eff6ff; }
.market-security-select { display: grid; flex: 1; min-width: 0; gap: 3px; border: 0; padding: 4px 0; color: inherit; text-align: left; background: transparent; cursor: pointer; }
.market-security-select:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
.market-security-item strong { color: #101828; font-size: 13px; }
.market-security-item small { overflow: hidden; color: #667085; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.market-security-item__meta { display: inline-flex; align-items: center; gap: 7px; flex: 0 0 auto; }
.market-icon-button { display: inline-grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 6px; color: #64748b; background: transparent; cursor: pointer; }
.market-icon-button:hover { color: #d97706; background: #fffbeb; }
</style>
