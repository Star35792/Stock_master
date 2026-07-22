<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { BarChart3, Check, Filter, Info, Trash2 } from "@lucide/vue";

import { fetchFactorDefinitions, fetchFactorProfiles } from "../api";
import type { FactorDefinition, FactorProfile } from "../types";

const definitions = ref<FactorDefinition[]>([]);
const selected = ref<FactorDefinition[]>([]);
const activeFactor = ref<FactorDefinition | null>(null);
const profiles = ref<FactorProfile[]>([]);
const search = ref("");
const frequency = ref("");
const isLoading = ref(false);
const isLoadingProfiles = ref(false);
const errorMessage = ref<string | null>(null);
const parameterValues = reactive<Record<string, Record<string, string | number>>>({});

const parameterLabels: Record<string, string> = {
  window: "计算窗口",
  window_days: "窗口天数",
  lag: "滞后期",
  quantile: "分位数",
  winsorize: "缩尾比例",
};

const factorCopy: Record<string, { name: string; description: string }> = {
  ret_20d: { name: "20 日收益率", description: "衡量标的近 20 个交易日的价格变化，用于观察短中期动量。" },
  market_valuation_pe_ttm: { name: "滚动市盈率", description: "比较当前价格与过去十二个月盈利，数值越低通常代表估值更低。" },
  net_profit_growth_yoy: { name: "净利润同比增长", description: "衡量公司净利润相对上年同期的增长幅度。" },
};

const filteredDefinitions = computed(() => definitions.value.filter((factor) => {
  const query = search.value.trim().toLowerCase();
  const matchesText = !query || [factor.name, factor.display_name ?? "", localizedName(factor)].some((value) => value.toLowerCase().includes(query));
  return matchesText && (!frequency.value || factor.frequency === frequency.value);
}));

function localizedName(factor: FactorDefinition) {
  return factorCopy[factor.name]?.name ?? factor.display_name ?? factor.name;
}

function localizedDescription(factor: FactorDefinition) {
  return factorCopy[factor.name]?.description ?? factor.description ?? "暂无中文说明，当前仅展示服务端定义和数据血缘。";
}

function isSelected(factor: FactorDefinition) {
  return selected.value.some((item) => item.id === factor.id);
}

function toggleFactor(factor: FactorDefinition) {
  if (isSelected(factor)) {
    selected.value = selected.value.filter((item) => item.id !== factor.id);
  } else {
    selected.value = [...selected.value, factor];
    parameterValues[factor.name] ??= Object.fromEntries(
      schemaProperties(factor).map(({ key, value }) => [key, String(value.default ?? "")]),
    );
  }
  activeFactor.value = factor;
}

function removeFactor(factor: FactorDefinition) {
  selected.value = selected.value.filter((item) => item.id !== factor.id);
  if (activeFactor.value?.id === factor.id) activeFactor.value = null;
}

function schemaProperties(factor: FactorDefinition | null) {
  const schema = factor?.parameters_schema;
  if (!schema || typeof schema !== "object") return [];
  const properties = schema.properties;
  if (properties && typeof properties === "object") {
    return Object.entries(properties as Record<string, Record<string, unknown>>).map(([key, value]) => ({ key, label: parameterLabels[key] ?? key, value }));
  }
  const result: Array<{ key: string; label: string; value: Record<string, unknown> }> = [];
  const components = Array.isArray(schema.components) ? schema.components : [];
  for (const component of components) {
    if (!component || typeof component !== "object") continue;
    const item = component as Record<string, unknown>;
    const name = String(item.factor_name ?? "component");
    result.push({ key: `weight:${name}`, label: `${factorCopy[name]?.name ?? name}权重`, value: { type: "number", default: item.weight ?? "" } });
  }
  for (const [key, value] of Object.entries(schema)) {
    if (key !== "components" && ["string", "number", "boolean"].includes(typeof value)) {
      result.push({ key, label: parameterLabels[key] ?? key, value: { type: typeof value === "number" ? "number" : "string", default: value } });
    }
  }
  return result;
}

async function loadDefinitions() {
  isLoading.value = true;
  errorMessage.value = null;
  try {
    const result = await fetchFactorDefinitions({ is_active: true });
    definitions.value = result.items;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "因子定义读取失败";
  } finally {
    isLoading.value = false;
  }
}

async function loadProfiles(factor: FactorDefinition) {
  activeFactor.value = factor;
  parameterValues[factor.name] ??= Object.fromEntries(
    schemaProperties(factor).map(({ key, value }) => [key, String(value.default ?? "")]),
  );
  isLoadingProfiles.value = true;
  try {
    const result = await fetchFactorProfiles({ factor_name: factor.name, factor_version: factor.version });
    profiles.value = result.items;
  } catch (error) {
    profiles.value = [];
    errorMessage.value = error instanceof Error ? error.message : "因子画像读取失败";
  } finally {
    isLoadingProfiles.value = false;
  }
}

watch(activeFactor, (factor) => {
  if (factor) void loadProfiles(factor);
});

onMounted(loadDefinitions);
</script>

<template>
  <section class="page-view factor-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">Factor Research</p>
        <h1>因子工作区</h1>
        <p class="page-subtitle">浏览因子定义、理解数据含义，并把需要的因子加入研究组合。</p>
      </div>
      <span class="readonly-pill"><Info :size="15" />当前只选择研究因子，不会自动交易</span>
    </header>

    <div class="factor-toolbar">
      <label class="search-field">搜索因子<input v-model="search" placeholder="代码、中文名称或描述" /></label>
      <label>频率<select v-model="frequency"><option value="">全部</option><option value="daily">日频</option><option value="weekly">周频</option><option value="monthly">月频</option></select></label>
      <button class="secondary-action" type="button" @click="loadDefinitions"><Filter :size="16" />{{ isLoading ? "读取中" : "刷新定义" }}</button>
    </div>

    <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
    <div class="factor-layout">
      <div class="factor-catalog">
        <div v-if="!filteredDefinitions.length && !isLoading" class="empty-panel">没有符合条件的因子。</div>
        <article v-for="factor in filteredDefinitions" :key="factor.id" class="factor-card" :class="{ active: activeFactor?.id === factor.id }" @click="loadProfiles(factor)">
          <div class="factor-card-top"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" :title="isSelected(factor) ? '移出选择' : '加入选择'" @click.stop="toggleFactor(factor)"><Check v-if="isSelected(factor)" :size="16" /><span v-else>+</span></button></div>
          <p>{{ localizedDescription(factor) }}</p>
          <footer><span>{{ factor.frequency === "daily" ? "日频" : factor.frequency === "weekly" ? "周频" : "月频" }}</span><span>{{ factor.applicable_security_types.join("、") || "未限制证券类型" }}</span><span v-if="factor.window_days">窗口 {{ factor.window_days }} 日</span></footer>
        </article>
      </div>

      <aside class="factor-selection">
        <div class="panel-heading"><div><h2>已选择因子</h2><span>{{ selected.length }} 个因子</span></div></div>
        <div v-if="!selected.length" class="empty-panel">从左侧因子卡片加入研究组合。</div>
        <ul v-else class="selection-list"><li v-for="factor in selected" :key="factor.id"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" title="移除因子" @click="removeFactor(factor)"><Trash2 :size="15" /></button></li></ul>

        <div v-if="activeFactor" class="factor-detail"><h2>{{ localizedName(activeFactor) }}</h2><p>{{ localizedDescription(activeFactor) }}</p><dl><div><dt>输入数据</dt><dd>{{ activeFactor.inputs.join("、") || "由服务端定义" }}</dd></div><div><dt>依赖</dt><dd>{{ activeFactor.dependencies.join("、") || "无" }}</dd></div><div><dt>适用类型</dt><dd>{{ activeFactor.applicable_security_types.join("、") || "未限制" }}</dd></div></dl><div v-if="schemaProperties(activeFactor).length" class="parameter-list"><strong>参数配置</strong><label v-for="parameter in schemaProperties(activeFactor)" :key="parameter.key">{{ parameter.label }}<input v-model="parameterValues[activeFactor.name][parameter.key]" :type="parameter.value.type === 'number' || parameter.value.type === 'integer' ? 'number' : 'text'" :placeholder="String(parameter.value.default ?? '')" /></label></div><div class="profile-summary"><strong>最近画像</strong><span v-if="isLoadingProfiles">读取中…</span><span v-else-if="!profiles.length">暂无画像数据</span><span v-else>覆盖率 {{ (Number(profiles[profiles.length - 1].coverage_rate) * 100).toFixed(1) }}% · IC {{ profiles[profiles.length - 1].ic_1d ?? "-" }}</span></div></div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.factor-page { max-width: 1240px; margin: 0 auto; padding: 30px 28px 72px; }
.page-heading, .factor-toolbar, .factor-card-top, .factor-card footer, .selection-list li { display: flex; align-items: center; }
.page-heading { justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.page-heading h1 { margin: 5px 0; font-size: clamp(28px, 4vw, 40px); }
.eyebrow { margin: 0; color: #2563eb; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.page-subtitle { margin: 0; color: #667085; font-size: 14px; }
.factor-toolbar { align-items: end; gap: 12px; padding: 16px; border: 1px solid #e3e7ee; border-radius: 8px; background: #fff; }
.factor-toolbar label, .parameter-list label { display: grid; gap: 6px; color: #667085; font-size: 12px; font-weight: 700; }
.search-field { flex: 1; }
input, select { min-height: 40px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; background: #fff; }
.factor-layout { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(300px, .8fr); gap: 16px; margin-top: 16px; align-items: start; }
.factor-catalog { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.factor-card, .factor-selection { border: 1px solid #e3e7ee; border-radius: 8px; background: #fff; }
.factor-card { padding: 16px; cursor: pointer; }
.factor-card.active { border-color: #7aa7ef; box-shadow: inset 0 0 0 1px #7aa7ef; }
.factor-card-top, .factor-card footer, .selection-list li { justify-content: space-between; gap: 10px; }
.factor-card code, .selection-list code { display: block; margin-top: 4px; color: #667085; font-size: 11px; }
.factor-card p, .factor-detail p { color: #667085; font-size: 13px; line-height: 1.55; }
.factor-card footer { justify-content: flex-start; flex-wrap: wrap; color: #667085; font-size: 11px; }
.factor-selection { padding: 18px; }
.selection-list { display: grid; gap: 8px; padding: 0; list-style: none; }
.selection-list li { padding: 10px 0; border-bottom: 1px solid #eef1f5; }
.icon-button { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid #d7dde6; border-radius: 6px; background: #fff; cursor: pointer; }
.factor-detail { margin-top: 18px; padding-top: 18px; border-top: 1px solid #e3e7ee; }
.factor-detail dl { display: grid; gap: 8px; }
.factor-detail dl div { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font-size: 12px; }
.factor-detail dt { color: #667085; }.factor-detail dd { margin: 0; }
.parameter-list { display: grid; gap: 10px; margin-top: 18px; }
.profile-summary { display: grid; gap: 5px; margin-top: 18px; padding: 12px; border-radius: 7px; background: #f8fafc; font-size: 12px; }
.empty-panel { padding: 24px; color: #667085; text-align: center; }
@media (max-width: 900px) { .factor-page { padding: 24px 16px 48px; }.factor-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .page-heading, .factor-toolbar { align-items: stretch; flex-direction: column; }.factor-catalog { grid-template-columns: 1fr; } }
</style>
