<script setup lang="ts">
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";

import { qualityStatusLabel } from "../../components/operations/formatters";
import type { DailyBarItem } from "../../types";

const props = defineProps<{ bars: DailyBarItem[]; loading: boolean; symbol: string }>();

const chartHost = ref<HTMLElement | null>(null);
const chart = shallowRef<IChartApi | null>(null);
const candleSeries = shallowRef<ISeriesApi<"Candlestick"> | null>(null);
const volumeSeries = shallowRef<ISeriesApi<"Histogram"> | null>(null);
const ma5Series = shallowRef<ISeriesApi<"Line"> | null>(null);
const ma10Series = shallowRef<ISeriesApi<"Line"> | null>(null);
const ma20Series = shallowRef<ISeriesApi<"Line"> | null>(null);
const selectedBar = ref<DailyBarItem | null>(null);
const chartError = ref("");
const visibleCount = ref<60 | 120 | 250 | "all">(120);
let resizeObserver: ResizeObserver | null = null;

const normalized = computed(() => {
  const unique = new Map<string, DailyBarItem>();
  let invalidCount = 0;
  for (const bar of props.bars) {
    const open = Number(bar.open);
    const high = Number(bar.high);
    const low = Number(bar.low);
    const close = Number(bar.close);
    const valid = [open, high, low, close].every((value) => Number.isFinite(value) && value > 0)
      && high >= Math.max(open, close)
      && low <= Math.min(open, close)
      && ["trusted", "manual_fixed"].includes(bar.quality_status)
      && !bar.is_suspect;
    if (!valid) {
      invalidCount += 1;
      continue;
    }
    unique.set(bar.trade_date, bar);
  }
  return {
    bars: [...unique.values()].sort((left, right) => left.trade_date.localeCompare(right.trade_date)),
    invalidCount,
  };
});

const displayedBar = computed(() => selectedBar.value ?? normalized.value.bars[normalized.value.bars.length - 1] ?? null);

// 涨跌额与涨跌幅优先使用后端字段，缺失时用 close - pre_close 自动补算，
// 保证 pre_close 可得时即使后端未给 pct_change/change_amount 也能显示涨跌。
const changeMetrics = computed(() => {
  const bar = displayedBar.value;
  if (!bar) return { amount: Number.NaN, pct: Number.NaN };
  const close = Number(bar.close);
  const preClose = bar.pre_close !== null ? Number(bar.pre_close) : Number.NaN;
  const statedAmount = bar.change_amount !== null ? Number(bar.change_amount) : Number.NaN;
  const statedPct = bar.pct_change !== null ? Number(bar.pct_change) : Number.NaN;
  const computedAmount = Number.isFinite(preClose) ? close - preClose : Number.NaN;
  const computedPct = Number.isFinite(preClose) && preClose !== 0
    ? (computedAmount / preClose) * 100
    : Number.NaN;
  return {
    amount: Number.isFinite(statedAmount) ? statedAmount : computedAmount,
    pct: Number.isFinite(statedPct) ? statedPct : computedPct,
  };
});

const directionClass = computed(() => {
  const { amount, pct } = changeMetrics.value;
  const delta = Number.isFinite(amount) ? amount : pct;
  return delta > 0 ? "up" : delta < 0 ? "down" : "flat";
});

function formatNumber(value: string | null, maximumFractionDigits = 2) {
  if (value === null || !Number.isFinite(Number(value))) return "-";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(Number(value));
}

function formatSignedFixed(value: number, suffix = "") {
  if (!Number.isFinite(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)}${suffix}`;
}

function adjustLabel(value: string) {
  return ({ none: "未复权", qfq: "前复权", hfq: "后复权" } as Record<string, string>)[value] ?? value;
}

function candleColor(bar: DailyBarItem) {
  const open = Number(bar.open);
  const close = Number(bar.close);
  return close > open ? "#e5484d" : close < open ? "#16a36a" : "#8b95a7";
}

function applyVisibleRange() {
  const api = chart.value;
  const length = normalized.value.bars.length;
  if (!api || !length) return;
  if (visibleCount.value === "all") {
    api.timeScale().fitContent();
    return;
  }
  const count = Math.min(visibleCount.value, length);
  api.timeScale().setVisibleRange({
    from: normalized.value.bars[length - count].trade_date as Time,
    to: normalized.value.bars[length - 1].trade_date as Time,
  });
}

function updateChart() {
  if (!chart.value || !candleSeries.value || !volumeSeries.value || !ma5Series.value || !ma10Series.value || !ma20Series.value) return;
  const bars = normalized.value.bars;
  candleSeries.value.setData(bars.map((bar) => ({
    time: bar.trade_date as Time,
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
    color: candleColor(bar),
    borderColor: candleColor(bar),
    wickColor: candleColor(bar),
  })));
  volumeSeries.value.setData(bars.flatMap((bar) => {
    if (bar.volume === null || !Number.isFinite(Number(bar.volume))) return [];
    return [{ time: bar.trade_date as Time, value: Number(bar.volume), color: `${candleColor(bar)}99` }];
  }));
  const movingAverage = (window: number) => bars.flatMap((bar, index) => {
    if (index + 1 < window) return [];
    const values = bars.slice(index + 1 - window, index + 1).map((item) => Number(item.close));
    return [{ time: bar.trade_date as Time, value: values.reduce((sum, value) => sum + value, 0) / window }];
  });
  ma5Series.value.setData(movingAverage(5));
  ma10Series.value.setData(movingAverage(10));
  ma20Series.value.setData(movingAverage(20));
  selectedBar.value = bars[bars.length - 1] ?? null;
  void nextTick(applyVisibleRange);
}

function selectRange(value: 60 | 120 | 250 | "all") {
  visibleCount.value = value;
  applyVisibleRange();
}

onMounted(() => {
  if (!chartHost.value) return;
  try {
    const instance = createChart(chartHost.value, {
      width: chartHost.value.clientWidth,
      height: chartHost.value.clientHeight || 450,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#667085", attributionLogo: true, panes: { separatorColor: "#e5e9f0", separatorHoverColor: "#94a3b8" } },
      grid: { vertLines: { color: "#f1f3f7" }, horzLines: { color: "#f1f3f7" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#e1e6ed", scaleMargins: { top: 0.08, bottom: 0.05 } },
      timeScale: { borderColor: "#e1e6ed", timeVisible: false, rightOffset: 4, barSpacing: 8, minBarSpacing: 3 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { locale: "zh-CN" },
    });
    chart.value = instance;
    candleSeries.value = instance.addSeries(CandlestickSeries, {
      upColor: "#e5484d", downColor: "#16a36a", borderUpColor: "#e5484d", borderDownColor: "#16a36a", wickUpColor: "#e5484d", wickDownColor: "#16a36a",
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
    }, 0);
    ma5Series.value = instance.addSeries(LineSeries, { color: "#d99a00", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
    ma10Series.value = instance.addSeries(LineSeries, { color: "#8b5cf6", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
    ma20Series.value = instance.addSeries(LineSeries, { color: "#2563eb", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
    volumeSeries.value = instance.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    }, 1);
    instance.priceScale("volume", 1).applyOptions({ scaleMargins: { top: 0.18, bottom: 0.02 } });
    instance.panes()[1]?.setHeight(105);
    instance.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const date = typeof param.time === "string"
        ? param.time
        : typeof param.time === "number"
          ? new Date(param.time * 1000).toISOString().slice(0, 10)
          : `${param.time.year}-${String(param.time.month).padStart(2, "0")}-${String(param.time.day).padStart(2, "0")}`;
      selectedBar.value = normalized.value.bars.find((bar) => bar.trade_date === date) ?? selectedBar.value;
    });
    resizeObserver = new ResizeObserver(([entry]) => {
      if (entry) {
        instance.applyOptions({
          width: Math.floor(entry.contentRect.width),
          height: Math.floor(entry.contentRect.height),
        });
        window.requestAnimationFrame(applyVisibleRange);
      }
    });
    resizeObserver.observe(chartHost.value);
    updateChart();
  } catch (error) {
    chartError.value = error instanceof Error ? error.message : "K 线图初始化失败";
  }
});

watch(() => props.bars, updateChart, { deep: true });

onUnmounted(() => {
  resizeObserver?.disconnect();
  chart.value?.remove();
});
</script>

<template>
  <section class="kline-shell" aria-label="日 K 线图">
    <header class="kline-toolbar">
      <div>
        <span class="market-kicker">日 K · {{ adjustLabel(displayedBar?.adjust_type ?? "none") }}</span>
        <strong>{{ symbol || "尚未选择证券" }}</strong>
      </div>
      <div class="range-switch" aria-label="可视区间">
        <button v-for="range in [60, 120, 250] as const" :key="range" type="button" :class="{ active: visibleCount === range }" @click="selectRange(range)">近 {{ range }} 日</button>
        <button type="button" :class="{ active: visibleCount === 'all' }" @click="selectRange('all')">全部</button>
      </div>
    </header>
    <div class="ma-legend" aria-label="均线图例"><span class="ma5">MA5</span><span class="ma10">MA10</span><span class="ma20">MA20</span></div>

    <div v-if="displayedBar" class="ohlcv-strip">
      <span>{{ displayedBar.trade_date }}</span>
      <span>开 <strong>{{ displayedBar.open }}</strong></span>
      <span>高 <strong>{{ displayedBar.high }}</strong></span>
      <span>低 <strong>{{ displayedBar.low }}</strong></span>
      <span>收 <strong>{{ displayedBar.close }}</strong></span>
      <span :class="directionClass">涨跌 {{ formatSignedFixed(changeMetrics.amount) }}（{{ formatSignedFixed(changeMetrics.pct, "%") }}）</span>
      <span>成交量 {{ formatNumber(displayedBar.volume, 0) }} 股/份</span>
      <span>成交额 {{ formatNumber(displayedBar.amount) }} 元</span>
      <span>{{ qualityStatusLabel(displayedBar.quality_status) }}</span>
    </div>

    <div class="chart-stage">
      <div ref="chartHost" class="chart-host" role="img" :aria-label="`${symbol} 日 K 线和成交量图`" />
      <div v-if="loading" class="chart-overlay">正在加载可信日线…</div>
      <div v-else-if="!normalized.bars.length" class="chart-overlay">暂无可绘制的可信日线。</div>
      <div v-else-if="chartError" class="chart-overlay chart-overlay--error">{{ chartError }}，请使用下方明细表查看数据。</div>
    </div>
    <p v-if="normalized.invalidCount" class="chart-warning">已排除 {{ normalized.invalidCount }} 条价格关系异常或非可消费记录。</p>
    <p class="chart-note">红涨绿跌；成交量副图与日 K 共用交易日轴。当前为未复权日线，公司行为可能造成机械跳空；数据非实时，也不是可成交报价。</p>
  </section>
</template>

<style scoped>
.kline-shell { display: grid; gap: 10px; min-width: 0; }
.kline-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.kline-toolbar > div:first-child { display: grid; gap: 4px; }
.market-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.kline-toolbar strong { color: #101828; font-size: 16px; }
.range-switch { display: flex; gap: 5px; overflow-x: auto; }
.range-switch button { min-height: 36px; flex: 0 0 auto; border: 1px solid #d7dce4; border-radius: 6px; padding: 0 10px; color: #475467; background: #fff; cursor: pointer; font-size: 11px; font-weight: 700; }
.range-switch button.active { border-color: #2563eb; color: #fff; background: #2563eb; }
.ma-legend { display: flex; gap: 14px; font-size: 11px; font-weight: 800; }.ma5 { color: #b77900; }.ma10 { color: #7c3aed; }.ma20 { color: #2563eb; }
.ohlcv-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 14px; min-height: 34px; padding: 7px 10px; border-radius: 6px; color: #667085; background: #f8fafc; font-size: 11px; }
.ohlcv-strip strong { color: #344054; }.ohlcv-strip .up { color: #d92d20; font-weight: 800; }.ohlcv-strip .down { color: #07875e; font-weight: 800; }.ohlcv-strip .flat { color: #667085; font-weight: 800; }
.chart-stage { position: relative; min-width: 0; overflow: hidden; border: 1px solid #e6eaf0; border-radius: 8px; background: #fff; }
.chart-host { width: 100%; height: 450px; touch-action: pan-y; }
.chart-overlay { position: absolute; inset: 0; display: grid; place-items: center; color: #667085; background: rgb(255 255 255 / 82%); font-size: 13px; }
.chart-overlay--error { color: #b42318; }.chart-warning { color: #9a3412; font-size: 12px; }.chart-note { color: #667085; font-size: 11px; line-height: 1.55; }
@media (max-width: 620px) { .kline-toolbar { align-items: flex-start; flex-direction: column; }.range-switch { width: 100%; }.chart-host { height: 380px; }.ohlcv-strip { align-items: flex-start; flex-direction: column; }.chart-note { font-size: 10px; } }
</style>
