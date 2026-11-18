<script setup lang="ts">
import { computed } from "vue";

type IcPoint = { trade_date: string; value: number | null };

const props = defineProps<{ points: IcPoint[]; metric: "ic_1d" | "rank_ic_1d" }>();

const valid = computed(() =>
  props.points
    .filter((point) => point.value !== null && Number.isFinite(point.value))
    .map((point) => ({ trade_date: point.trade_date, value: point.value as number })),
);

const latest = computed(() => valid.value[valid.value.length - 1] ?? null);

const geometry = computed(() => {
  const values = valid.value.map((point) => point.value);
  if (values.length < 2) return null;
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const span = maximum - minimum || 1;
  const width = 720;
  const height = 160;
  const paddingX = 8;
  const paddingTop = 10;
  const paddingBottom = 20;
  const innerWidth = width - paddingX * 2;
  const innerHeight = height - paddingTop - paddingBottom;
  const points = values.map((value, index) => {
    const x = paddingX + (index / (values.length - 1)) * innerWidth;
    const y = paddingTop + (1 - (value - minimum) / span) * innerHeight;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const zeroY = paddingTop + (1 - (0 - minimum) / span) * innerHeight;
  return { points, zeroY, minimum, maximum, width, height };
});

function formatIc(value: number): string {
  return value.toFixed(3);
}

function metricLabel(): string {
  return props.metric === "rank_ic_1d" ? "秩 IC(1d)" : "IC(1d)";
}

function axisValue(value: number): string {
  return value.toFixed(2);
}
</script>

<template>
  <div class="ic-trend" :aria-label="`${metricLabel()} 时序趋势`">
    <div class="ic-trend-header">
      <strong>{{ metricLabel() }} 趋势</strong>
      <span v-if="latest" :class="latest.value >= 0 ? 'ic-up' : 'ic-down'">最新 {{ formatIc(latest.value) }} · {{ latest.trade_date }}</span>
    </div>
    <div v-if="!geometry" class="ic-trend-empty">暂无可绘制的 {{ metricLabel() }} 序列（需至少两个有效数据点）。</div>
    <svg v-else :viewBox="`0 0 ${geometry.width} ${geometry.height}`" preserveAspectRatio="none" role="img" :aria-label="`${metricLabel()} 时序折线图`">
      <line :x1="8" :x2="geometry.width - 8" :y1="geometry.zeroY" :y2="geometry.zeroY" stroke="#cbd5e1" stroke-dasharray="4 4" />
      <polyline :points="geometry.points" fill="none" stroke="#2563eb" stroke-width="2" vector-effect="non-scaling-stroke" />
      <text :x="8" :y="14" fill="#667085" font-size="11">{{ axisValue(geometry.maximum) }}</text>
      <text :x="8" :y="geometry.height - 4" fill="#667085" font-size="11">{{ axisValue(geometry.minimum) }}</text>
    </svg>
    <p class="ic-trend-note">横轴为画像交易日；虚线为 IC = 0 参考线。IC 用于诊断因子横截面区分度，正值表示同向、负值表示反向，不自动决定方向。</p>
  </div>
</template>

<style scoped>
.ic-trend { display: grid; gap: 6px; min-width: 0; }
.ic-trend-header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ic-trend-header strong { color: #344054; font-size: 12px; }
.ic-trend-header span { font-size: 11px; font-variant-numeric: tabular-nums; }
.ic-up { color: #d92d20; font-weight: 700; }
.ic-down { color: #07875e; font-weight: 700; }
.ic-trend-empty { color: #667085; font-size: 12px; }
.ic-trend svg { width: 100%; height: auto; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 6px; }
.ic-trend-note { color: #98a2b3; font-size: 10px; line-height: 1.5; margin: 0; }
</style>
