<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
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
  if (!properties || typeof properties !== "object") return [];
  return Object.entries(properties as Record<string, Record<string, unknown>>).map(([key, value]) => ({ key, value }));
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

        <div v-if="activeFactor" class="factor-detail"><h2>{{ localizedName(activeFactor) }}</h2><p>{{ localizedDescription(activeFactor) }}</p><dl><div><dt>输入数据</dt><dd>{{ activeFactor.inputs.join("、") || "由服务端定义" }}</dd></div><div><dt>依赖</dt><dd>{{ activeFactor.dependencies.join("、") || "无" }}</dd></div><div><dt>适用类型</dt><dd>{{ activeFactor.applicable_security_types.join("、") || "未限制" }}</dd></div></dl><div v-if="schemaProperties(activeFactor).length" class="parameter-list"><strong>参数</strong><label v-for="parameter in schemaProperties(activeFactor)" :key="parameter.key">{{ parameter.key }}<input :type="parameter.value.type === 'number' || parameter.value.type === 'integer' ? 'number' : 'text'" :placeholder="String(parameter.value.default ?? '')" /></label></div><div class="profile-summary"><strong>最近画像</strong><span v-if="isLoadingProfiles">读取中…</span><span v-else-if="!profiles.length">暂无画像数据</span><span v-else>覆盖率 {{ (Number(profiles[profiles.length - 1].coverage_rate) * 100).toFixed(1) }}% · IC {{ profiles[profiles.length - 1].ic_1d ?? "-" }}</span></div></div>
      </aside>
    </div>
  </section>
</template>
