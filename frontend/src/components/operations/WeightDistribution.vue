<script setup lang="ts">
import { computed } from "vue";

type Slice = { key: string; weight: number; items: string[] };

const props = defineProps<{
  /** 行业权重分布，weight 为比例（0~1） */
  slices: Slice[];
  /** 分布名称，例如「行业」或「标的」 */
  label: string;
}>();

const palette = [
  "#2563eb", "#16a36a", "#d99a00", "#8b5cf6", "#e5484d",
  "#0ea5e9", "#f59e0b", "#10b981", "#6366f1", "#ec4899",
];

const ordered = computed(() => [...props.slices].sort((left, right) => right.weight - left.weight));

function colorFor(index: number): string {
  return palette[index % palette.length];
}

function formatWeight(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

// 水平条形图几何：每条占 28px，条宽按权重等比映射到 100%
function barWidth(value: number): string {
  const max = ordered.value[0]?.weight || 0;
  if (max <= 0) return "0%";
  return `${((value / max) * 100).toFixed(2)}%`;
}
</script>

<template>
  <div class="weight-distribution" :aria-label="`${label}权重分布`">
    <div v-if="!ordered.length" class="weight-empty">暂无{{ label }}权重数据。</div>
    <div v-else class="weight-list">
      <div v-for="(slice, index) in ordered" :key="slice.key" class="weight-row">
        <span class="weight-key" :title="slice.items.join('、')">{{ slice.key }}</span>
        <div class="weight-bar-track">
          <div
            class="weight-bar"
            :style="{ width: barWidth(slice.weight), backgroundColor: colorFor(index) }"
          />
        </div>
        <span class="weight-value">{{ formatWeight(slice.weight) }}</span>
      </div>
    </div>
    <div class="weight-legend">
      <span v-for="(slice, index) in ordered.slice(0, 10)" :key="slice.key" class="weight-legend-item">
        <i :style="{ backgroundColor: colorFor(index) }" aria-hidden="true" />
        {{ slice.key }}
      </span>
      <span v-if="ordered.length > 10" class="weight-legend-more">等 {{ ordered.length }} 项</span>
    </div>
  </div>
</template>

<style scoped>
.weight-distribution { display: grid; gap: 10px; min-width: 0; }
.weight-empty { color: #667085; font-size: 13px; }
.weight-list { display: grid; gap: 8px; }
.weight-row { display: grid; grid-template-columns: 120px 1fr 64px; align-items: center; gap: 10px; }
.weight-key { color: #344054; font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.weight-bar-track { height: 14px; background: #f1f3f7; border-radius: 7px; overflow: hidden; }
.weight-bar { height: 100%; border-radius: 7px; transition: width .2s ease; }
.weight-value { color: #475467; font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
.weight-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; }
.weight-legend-item { display: inline-flex; align-items: center; gap: 5px; color: #667085; font-size: 11px; }
.weight-legend-item i { width: 9px; height: 9px; border-radius: 2px; }
.weight-legend-more { color: #98a2b3; font-size: 11px; }
@media (max-width: 620px) { .weight-row { grid-template-columns: 90px 1fr 58px; } }
</style>
