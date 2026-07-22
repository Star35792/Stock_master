<script setup lang="ts">
import { AlertTriangle, BarChart3, CircleHelp, Star } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";

import { formatPctChange, qualityStatusLabel, securityTypeLabel } from "../components/operations/formatters";
import type { DailyBarItem } from "../types";
import MarketBarsPanel from "./components/MarketBarsPanel.vue";
import MarketSearchPanel, { type SecurityItem } from "./components/MarketSearchPanel.vue";
import WatchlistPanel from "./components/WatchlistPanel.vue";

type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type Summary = {
  symbol: string; name: string; security_type: string; exchange: string; currency: string;
  industry: string | null; latest_trade_date: string | null; latest: DailyBarItem | null;
  data_status: "available" | "unavailable"; data_note: string;
};

const WATCHLIST_KEY = "stock-master.market.watchlist";
const keyword = ref("");
const securities = ref<SecurityItem[]>([]);
const selectedSymbol = ref("");
const summary = ref<Summary | null>(null);
const bars = ref<DailyBarItem[]>([]);
const chartBars = ref<DailyBarItem[]>([]);
const qualityStatus = ref("consumable");
const startDate = ref("");
const endDate = ref("");
const loadingSearch = ref(false);
const loadingMarket = ref(false);
const errorMessage = ref("");
const watchlist = ref<string[]>(readWatchlist());
let marketRequestId = 0;

const selectedLabel = computed(() => summary.value ? `${summary.value.name} · ${summary.value.symbol}` : "尚未选择证券");

function readWatchlist(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(WATCHLIST_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 50) : [];
  } catch { return []; }
}

function persistWatchlist() { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist.value)); }

function toggleWatchlist(symbol: string) {
  watchlist.value = watchlist.value.includes(symbol) ? watchlist.value.filter(item => item !== symbol) : [symbol, ...watchlist.value].slice(0, 50);
  persistWatchlist();
}

function removeWatchlist(symbol: string) { watchlist.value = watchlist.value.filter(item => item !== symbol); persistWatchlist(); }

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(body?.detail ?? `请求失败（${response.status}）`);
  return body as T;
}

async function searchSecurities() {
  loadingSearch.value = true; errorMessage.value = "";
  try {
    const params = new URLSearchParams({ limit: "20", offset: "0" });
    if (keyword.value.trim()) params.set("keyword", keyword.value.trim());
    const page = await requestJson<Page<SecurityItem>>(`/api/v1/securities?${params}`);
    securities.value = page.items;
    if (!page.items.some((item) => item.symbol === selectedSymbol.value)) {
      const defaultItem = page.items.find((item) => item.security_type === "stock") ?? page.items[0];
      if (defaultItem) await selectSecurity(defaultItem);
      else { selectedSymbol.value = ""; summary.value = null; bars.value = []; chartBars.value = []; }
    }
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : "证券搜索失败"; securities.value = []; }
  finally { loadingSearch.value = false; }
}

async function selectSecurity(item: SecurityItem) { selectedSymbol.value = item.symbol; await loadMarket(); }
async function selectWatchlist(symbol: string) { selectedSymbol.value = symbol; await loadMarket(); }

async function loadMarket() {
  if (!selectedSymbol.value) return;
  if (startDate.value && endDate.value && startDate.value > endDate.value) {
    errorMessage.value = "起始日期不能晚于结束日期。";
    return;
  }
  const requestId = ++marketRequestId;
  const requestedSymbol = selectedSymbol.value;
  loadingMarket.value = true; errorMessage.value = ""; summary.value = null; chartBars.value = []; bars.value = [];
  try {
    const params = new URLSearchParams({ adjust_type: "none" });
    if (startDate.value) params.set("start_date", startDate.value);
    if (endDate.value) params.set("end_date", endDate.value);
    if (qualityStatus.value) params.set("quality_status", qualityStatus.value);
    const chartParams = new URLSearchParams({ adjust_type: "none", limit: "320" });
    if (startDate.value) chartParams.set("start_date", startDate.value);
    if (endDate.value) chartParams.set("end_date", endDate.value);
    const [summaryResult, chartResult, barsResult] = await Promise.all([
      requestJson<Summary>(`/api/v1/daily-bars/${encodeURIComponent(requestedSymbol)}/summary?adjust_type=none`),
      requestJson<Page<DailyBarItem>>(`/api/v1/daily-bars/${encodeURIComponent(requestedSymbol)}/chart?${chartParams}`),
      requestJson<Page<DailyBarItem>>(`/api/v1/daily-bars/${encodeURIComponent(requestedSymbol)}?${params}`),
    ]);
    if (requestId !== marketRequestId || requestedSymbol !== selectedSymbol.value) return;
    summary.value = summaryResult; chartBars.value = chartResult.items; bars.value = barsResult.items;
  } catch (error) {
    if (requestId !== marketRequestId) return;
    errorMessage.value = error instanceof Error ? error.message : "行情加载失败"; summary.value = null; chartBars.value = []; bars.value = [];
  } finally {
    if (requestId === marketRequestId) loadingMarket.value = false;
  }
}

onMounted(() => { void searchSecurities(); });
</script>

<template>
  <main class="market-page">
    <header class="market-page__hero">
      <div>
        <span class="market-page__eyebrow"><BarChart3 :size="15" />行情工作台 / DAILY</span>
        <h1>负责人的看盘台</h1>
        <p>搜索证券、查看可信日线和质量状态；行情来源为本地日线数据，不声称实时行情。</p>
      </div>
      <div class="market-hero-actions">
        <span class="market-readonly-pill"><Star :size="14" />自选仅保存在本浏览器</span>
        <a href="#market-security-search" class="market-search-jump">搜索 / 切换证券</a>
      </div>
    </header>
    <div v-if="errorMessage" class="market-page__error" role="alert"><AlertTriangle :size="17" />{{ errorMessage }}</div>
    <section class="market-layout">
      <aside class="market-sidebar">
        <MarketSearchPanel v-model:keyword="keyword" :items="securities" :loading="loadingSearch" :selected-symbol="selectedSymbol" :watchlist="watchlist" @search="searchSecurities" @select="selectSecurity" @toggle-watchlist="toggleWatchlist" />
        <WatchlistPanel :symbols="watchlist" :selected-symbol="selectedSymbol" @select="selectWatchlist" @remove="removeWatchlist" />
      </aside>
      <section class="market-content">
        <section class="market-summary market-panel">
          <div v-if="!summary" class="market-empty"><CircleHelp :size="22" /><span>{{ selectedSymbol ? "正在加载行情摘要" : "从左侧选择证券后查看摘要" }}</span></div>
          <template v-else>
            <header class="market-summary__header"><div><span class="market-kicker">行情摘要</span><h2>{{ selectedLabel }}</h2><p>{{ securityTypeLabel(summary.security_type) }} · {{ summary.exchange }} · {{ summary.industry || "行业未标注" }}</p></div><span class="market-data-note">{{ summary.data_note }}</span></header>
            <div v-if="summary.latest" class="market-summary__metrics"><div><span>最新收盘</span><strong>{{ summary.latest.close }}</strong></div><div><span>涨跌幅</span><strong>{{ formatPctChange(summary.latest.pct_change) }}</strong></div><div><span>交易日</span><strong>{{ summary.latest_trade_date }}</strong></div><div><span>质量状态</span><strong>{{ qualityStatusLabel(summary.latest.quality_status) }}</strong></div></div>
            <div v-else class="market-empty">暂无可信日线摘要，当前证券不能提供可用的本地行情快照。</div>
          </template>
        </section>
        <MarketBarsPanel :symbol="selectedSymbol" :bars="bars" :chart-bars="chartBars" :quality-status="qualityStatus" :start-date="startDate" :end-date="endDate" :loading="loadingMarket" @update:quality-status="qualityStatus = $event" @update:start-date="startDate = $event" @update:end-date="endDate = $event" @query="loadMarket" />
      </section>
    </section>
  </main>
</template>

<style scoped>
.market-page { max-width: 1240px; margin: 0 auto; padding: 30px 28px 72px; color: #172033; }
.market-page__hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.market-page__hero > div:first-child { display: grid; gap: 8px; }
.market-hero-actions { display: flex; align-items: flex-end; gap: 8px; }
.market-page__eyebrow { display: inline-flex; align-items: center; gap: 7px; color: #2563eb; font-size: 12px; font-weight: 800; letter-spacing: .08em; }
.market-page h1 { color: #101828; font-size: clamp(28px, 4vw, 40px); letter-spacing: 0; }
.market-page__hero p { color: #667085; font-size: 14px; }
.market-readonly-pill { display: inline-flex; align-items: center; gap: 7px; border: 1px solid #f5d58b; border-radius: 999px; padding: 8px 11px; color: #946200; background: #fffaf0; font-size: 12px; font-weight: 700; white-space: nowrap; }
.market-search-jump { display: inline-flex; min-height: 34px; align-items: center; border: 1px solid #bfdbfe; border-radius: 999px; padding: 0 11px; color: #1d4ed8; background: #eff6ff; font-size: 12px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.market-page__error { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; padding: 10px 12px; border: 1px solid #fecaca; border-radius: 7px; color: #b42318; background: #fff1f2; font-size: 13px; }
.market-layout { display: grid; grid-template-columns: minmax(290px, .72fr) minmax(0, 1.6fr); gap: 16px; align-items: start; }
.market-sidebar, .market-content { display: grid; gap: 16px; min-width: 0; }
.market-panel { min-width: 0; padding: 18px; border: 1px solid #e3e7ee; border-radius: 8px; background: #fff; }
.market-summary { min-height: 160px; }
.market-summary__header { display: flex; justify-content: space-between; gap: 16px; }
.market-summary__header > div { display: grid; gap: 5px; }
.market-summary h2 { color: #101828; font-size: 20px; }
.market-summary p, .market-data-note { color: #667085; font-size: 12px; }
.market-data-note { max-width: 290px; line-height: 1.5; text-align: right; }
.market-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.market-summary__metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; margin-top: 18px; }
.market-summary__metrics div { padding: 11px; border: 1px solid #eef1f5; border-radius: 7px; background: #fbfcfe; }
.market-summary__metrics span, .market-summary__metrics strong { display: block; }
.market-summary__metrics span { color: #667085; font-size: 11px; }
.market-summary__metrics strong { margin-top: 5px; color: #101828; font-size: 15px; }
.market-empty { display: flex; align-items: center; gap: 10px; min-height: 100px; color: #667085; font-size: 13px; }
@media (max-width: 900px) { .market-page { padding: 24px 16px 48px; } .market-layout { grid-template-columns: 1fr; } .market-content { grid-row: 1; }.market-sidebar { grid-row: 2; } }
@media (max-width: 620px) { .market-page__hero, .market-summary__header, .market-hero-actions { align-items: flex-start; flex-direction: column; } .market-data-note { max-width: none; text-align: left; } .market-summary__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
