<script setup lang="ts">
import {
  AreaSeries,
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

export type EquityPoint = { trade_date: string; total_equity: string };

const props = defineProps<{ points: EquityPoint[]; initialCash: string; loading: boolean }>();

const chartHost = ref<HTMLElement | null>(null);
const chart = shallowRef<IChartApi | null>(null);
const equitySeries = shallowRef<ISeriesApi<"Line"> | null>(null);
const drawdownSeries = shallowRef<ISeriesApi<"Histogram"> | null>(null);
const chartError = ref("");
const visibleCount = ref<60 | 120 | 250 | "all">(120);
const selectedIndex = ref<number>(-1);
let resizeObserver: ResizeObserver | null = null;

type Normalized = {
  dates: string[];
  equity: number[];
  drawdown: number[];
};

const normalized = computed<Normalized>(() => {
  const initial = Number(props.initialCash);
  if (!Number.isFinite(initial) || initial <= 0) return { dates: [], equity: [], drawdown: [] };
  const dates: string[] = [];
  const equity: number[] = [];
  const drawdown: number[] = [];
  let peak = initial;
  for (const point of props.points) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(point.trade_date)) continue;
    if (dates.length && point.trade_date <= dates[dates.length - 1]) continue;
    const value = Number(point.total_equity);
    if (!Number.isFinite(value) || value <= 0) continue;
    peak = Math.max(peak, value);
    dates.push(point.trade_date);
    equity.push(value);
    drawdown.push(value / peak - 1);
  }
  return { dates, equity, drawdown };
});

const selectedPoint = computed<{ date: string; equity: number; drawdown: number } | null>(() => {
  if (selectedIndex.value < 0 || selectedIndex.value >= normalized.value.dates.length) return null;
  return {
    date: normalized.value.dates[selectedIndex.value],
    equity: normalized.value.equity[selectedIndex.value],
    drawdown: normalized.value.drawdown[selectedIndex.value],
  };
});

const latestPoint = computed(() => {
  const data = normalized.value;
  if (!data.dates.length) return null;
  const index = data.dates.length - 1;
  return { date: data.dates[index], equity: data.equity[index], drawdown: data.drawdown[index] };
});

function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

function formatEquity(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

function applyVisibleRange() {
  const api = chart.value;
  const length = normalized.value.dates.length;
  if (!api || !length) return;
  if (visibleCount.value === "all") {
    api.timeScale().fitContent();
    return;
  }
  const count = Math.min(visibleCount.value, length);
  api.timeScale().setVisibleRange({
    from: normalized.value.dates[length - count] as Time,
    to: normalized.value.dates[length - 1] as Time,
  });
}

function updateChart() {
  if (!chart.value || !equitySeries.value || !drawdownSeries.value) return;
  const data = normalized.value;
  equitySeries.value.setData(data.dates.map((date, index) => ({ time: date as Time, value: data.equity[index] })));
  drawdownSeries.value.setData(data.dates.map((date, index) => ({
    time: date as Time,
    value: data.drawdown[index] * 100,
    color: data.drawdown[index] <= 0 ? "#16a36a" : "#e5484d",
  })));
  selectedIndex.value = data.dates.length ? data.dates.length - 1 : -1;
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
      height: chartHost.value.clientHeight || 480,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#667085",
        attributionLogo: true,
        panes: { separatorColor: "#e5e9f0", separatorHoverColor: "#94a3b8" },
      },
      grid: { vertLines: { color: "#f1f3f7" }, horzLines: { color: "#f1f3f7" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#e1e6ed", scaleMargins: { top: 0.08, bottom: 0.05 } },
      timeScale: { borderColor: "#e1e6ed", timeVisible: false, rightOffset: 4, barSpacing: 8, minBarSpacing: 3 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { locale: "zh-CN" },
    });
    chart.value = instance;
    equitySeries.value = instance.addSeries(LineSeries, {
      color: "#2563eb",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    }, 0);
    drawdownSeries.value = instance.addSeries(HistogramSeries, {
      priceFormat: { type: "percent", precision: 2, minMove: 0.01 },
      priceScaleId: "drawdown",
      lastValueVisible: false,
    }, 1);
    instance.priceScale("drawdown", 1).applyOptions({ scaleMargins: { top: 0.18, bottom: 0.02 } });
    instance.panes()[1]?.setHeight(110);
    instance.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const date = typeof param.time === "string"
        ? param.time
        : typeof param.time === "number"
          ? new Date(param.time * 1000).toISOString().slice(0, 10)
          : `${param.time.year}-${String(param.time.month).padStart(2, "0")}-${String(param.time.day).padStart(2, "0")}`;
      const index = normalized.value.dates.indexOf(date);
      if (index >= 0) selectedIndex.value = index;
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
    chartError.value = error instanceof Error ? error.message : "净值图初始化失败";
  }
});

watch(() => props.points, updateChart, { deep: true });

onUnmounted(() => {
  resizeObserver?.disconnect();
  chart.value?.remove();
});
</script>

<template>
  <section class="equity-shell" aria-label="回测净值与回撤图">
    <header class="equity-toolbar">
      <div>
        <span class="equity-kicker">回测净值 · 归一化起点 {{ formatEquity(Number(initialCash) || 0) }}</span>
        <strong v-if="latestPoint">期末权益 {{ formatEquity(latestPoint.equity) }}</strong>
        <strong v-else>暂无可绘制净值</strong>
      </div>
      <div class="range-switch" aria-label="可视区间">
        <button v-for="range in [60, 120, 250] as const" :key="range" type="button" :class="{ active: visibleCount === range }" @click="selectRange(range)">近 {{ range }} 日</button>
        <button type="button" :class="{ active: visibleCount === 'all' }" @click="selectRange('all')">全部</button>
      </div>
    </header>

    <div class="equity-legend" aria-label="图例"><span class="equity">策略净值</span><span class="drawdown">回撤（右轴）</span></div>

    <div v-if="selectedPoint" class="equity-strip">
      <span>{{ selectedPoint.date }}</span>
      <span>权益 <strong>{{ formatEquity(selectedPoint.equity) }}</strong></span>
      <span :class="selectedPoint.drawdown <= 0 ? 'down' : 'up'">回撤 {{ signedPercent(selectedPoint.drawdown) }}</span>
    </div>

    <div class="chart-stage">
      <div ref="chartHost" class="chart-host" role="img" aria-label="回测净值与回撤走势图" />
      <div v-if="loading" class="chart-overlay">正在加载净值…</div>
      <div v-else-if="!normalized.dates.length" class="chart-overlay">暂无可绘制的净值记录。</div>
      <div v-else-if="chartError" class="chart-overlay chart-overlay--error">{{ chartError }}，请使用下方明细表查看数据。</div>
    </div>
    <p class="chart-note">净值为主图（蓝线），回撤为副图（右轴，负值为绿、正值为红）。数据非实时，也不构成收益认证。</p>
  </section>
</template>

<style scoped>
.equity-shell { display: grid; gap: 10px; min-width: 0; }
.equity-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.equity-toolbar > div:first-child { display: grid; gap: 4px; }
.equity-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.equity-toolbar strong { color: #101828; font-size: 16px; }
.range-switch { display: flex; gap: 5px; overflow-x: auto; }
.range-switch button { min-height: 36px; flex: 0 0 auto; border: 1px solid #d7dce4; border-radius: 6px; padding: 0 10px; color: #475467; background: #fff; cursor: pointer; font-size: 11px; font-weight: 700; }
.range-switch button.active { border-color: #2563eb; color: #fff; background: #2563eb; }
.equity-legend { display: flex; gap: 14px; font-size: 11px; font-weight: 800; }
.equity { color: #2563eb; }
.drawdown { color: #16a36a; }
.equity-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 14px; min-height: 34px; padding: 7px 10px; border-radius: 6px; color: #667085; background: #f8fafc; font-size: 11px; }
.equity-strip strong { color: #344054; }
.equity-strip .up { color: #d92d20; font-weight: 800; }
.equity-strip .down { color: #07875e; font-weight: 800; }
.chart-stage { position: relative; min-width: 0; overflow: hidden; border: 1px solid #e6eaf0; border-radius: 8px; background: #fff; }
.chart-host { width: 100%; height: 480px; touch-action: pan-y; }
.chart-overlay { position: absolute; inset: 0; display: grid; place-items: center; color: #667085; background: rgb(255 255 255 / 82%); font-size: 13px; }
.chart-overlay--error { color: #b42318; }
.chart-note { color: #667085; font-size: 11px; line-height: 1.55; }
@media (max-width: 620px) { .equity-toolbar { align-items: flex-start; flex-direction: column; }.range-switch { width: 100%; }.chart-host { height: 380px; }.equity-strip { align-items: flex-start; flex-direction: column; }.chart-note { font-size: 10px; } }
</style>
