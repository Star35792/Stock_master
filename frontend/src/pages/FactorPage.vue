<script setup lang="ts">
import { BarChart3, Check, Filter, Info, Play, Trash2 } from "@lucide/vue";
import { onMounted } from "vue";

import { securityTypeLabel } from "../components/operations/formatters";
import { createFactorWorkspace } from "../composables/factorWorkspace";
import { useResearchStore } from "../stores/research";
import IcTrendChart from "./components/IcTrendChart.vue";

const researchStore = useResearchStore();

const {
  backtestForm,
  isRunningBacktest,
  backtestError,
  preflight,
  preflightError,
  preflightLoading,
  selected,
  missingDirectionKeys,
  selectionPanel,
  backtestResult,
  backtestFactorSnapshot,
  backtestHoldings,
  isLoadingHoldings,
  definitions,
  activeFactor,
  profiles,
  search,
  frequency,
  category,
  isLoading,
  isLoadingProfiles,
  errorMessage,
  parameterValues,
  factorKey,
  factorCategoryOptions,
  profileOverlapsBacktest,
  icMetric,
  icPoints,
  filteredDefinitions,
  isSelected,
  toggleFactor,
  removeFactor,
  schemaProperties,
  loadDefinitions,
  loadProfiles,
  localizedName,
  localizedDescription,
  categoryLabel,
  checkBacktestReadiness,
  runBacktest,
  loadBacktestHoldings,
} = createFactorWorkspace(undefined, {
  onBacktestRun: ({ runId, name, directions, benchmark }) => {
    researchStore.setLastRun(runId, name);
    researchStore.setFactorDirections(directions);
    researchStore.setBenchmark(benchmark);
  },
});

onMounted(loadDefinitions);
</script>

<template>
  <section class="page-view factor-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">因子研究</p>
        <h1>因子工作区</h1>
        <p class="page-subtitle">浏览因子定义、理解数据含义，并把需要的因子加入研究组合。</p>
      </div>
      <span class="readonly-pill"><Info :size="15" />当前只选择研究因子，不会自动交易</span>
    </header>

    <div class="factor-toolbar">
      <label class="search-field">搜索因子<input v-model="search" placeholder="代码、中文名称或描述" /></label>
      <label>频率<select v-model="frequency"><option value="">全部</option><option value="daily">日频</option><option value="weekly">周频</option><option value="monthly">月频</option></select></label>
      <label>分类<select v-model="category"><option v-for="opt in factorCategoryOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option></select></label>
      <button class="secondary-action" type="button" @click="loadDefinitions"><Filter :size="16" />{{ isLoading ? "读取中" : "刷新定义" }}</button>
    </div>

    <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
    <div class="factor-layout">
      <div class="factor-catalog">
        <div v-if="!filteredDefinitions.length && !isLoading" class="empty-panel">没有符合条件的因子。</div>
        <article v-for="factor in filteredDefinitions" :key="factor.id" class="factor-card" :class="{ active: activeFactor?.id === factor.id }" @click="loadProfiles(factor)">
          <div class="factor-card-top"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" :title="isSelected(factor) ? '移出选择' : '加入选择'" @click.stop="toggleFactor(factor)"><Check v-if="isSelected(factor)" :size="16" /><span v-else>+</span></button></div>
          <p>{{ localizedDescription(factor) }}</p>
          <footer><span class="category-tag">{{ categoryLabel(factor) }}</span><span>{{ factor.frequency === "daily" ? "日频" : factor.frequency === "weekly" ? "周频" : "月频" }}</span><span>{{ factor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制证券类型" }}</span><span v-if="factor.window_days">窗口 {{ factor.window_days }} 日</span></footer>
        </article>
      </div>

      <aside ref="selectionPanel" class="factor-selection">
        <div class="panel-heading"><div><h2>已选择因子</h2><span>{{ selected.length }} 个因子</span></div></div>
        <div v-if="!selected.length" class="empty-panel">从左侧因子卡片加入研究组合。</div>
        <template v-else>
          <p class="direction-guidance">方向是本次回测预先设定的研究假设，决定横截面排序；请勿根据本次回测结果反向挑选。</p>
          <ul class="selection-list">
            <li
              v-for="item in selected"
              :key="factorKey(item.factor)"
              :class="{ 'direction-missing': missingDirectionKeys.includes(factorKey(item.factor)) }"
            >
              <div class="selected-factor-copy">
                <strong>{{ localizedName(item.factor) }}</strong>
                <code>{{ item.factor.name }} · {{ item.factor.version }}</code>
              </div>
              <label class="direction-field">
                <span>排序方向</span>
                <select
                  v-model="item.direction"
                  class="direction-select"
                  :aria-invalid="missingDirectionKeys.includes(factorKey(item.factor))"
                  :aria-label="`${localizedName(item.factor)}排序方向`"
                >
                  <option value="unconfigured" disabled>请选择方向</option>
                  <option value="higher">数值越高越优先</option>
                  <option value="lower">数值越低越优先</option>
                </select>
              </label>
              <button class="icon-button" type="button" title="移除因子" @click="removeFactor(item.factor)"><Trash2 :size="15" /></button>
            </li>
          </ul>
        </template>

        <div v-if="activeFactor" class="factor-detail"><h2>{{ localizedName(activeFactor) }}</h2><p>{{ localizedDescription(activeFactor) }}</p><dl><div><dt>输入数据</dt><dd>{{ activeFactor.inputs.join("、") || "由服务端定义" }}</dd></div><div><dt>依赖</dt><dd>{{ activeFactor.dependencies.join("、") || "无" }}</dd></div><div><dt>适用类型</dt><dd>{{ activeFactor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制" }}</dd></div></dl><div v-if="schemaProperties(activeFactor).length" class="parameter-list"><strong>参数配置</strong><label v-for="parameter in schemaProperties(activeFactor)" :key="parameter.key">{{ parameter.label }}<input v-model="parameterValues[activeFactor.name][parameter.key]" :type="parameter.value.type === 'number' || parameter.value.type === 'integer' ? 'number' : 'text'" :placeholder="String(parameter.value.default ?? '')" /></label></div><div class="profile-summary"><strong>最近画像</strong><span v-if="isLoadingProfiles">读取中…</span><span v-else-if="!profiles.length">暂无画像数据</span><template v-else><span>截至 {{ profiles[profiles.length - 1].trade_date }} · 覆盖率 {{ (Number(profiles[profiles.length - 1].coverage_rate) * 100).toFixed(1) }}% · IC {{ profiles[profiles.length - 1].ic_1d ?? "-" }}</span><span class="profile-caveat">仅用于诊断，不会自动设置方向；可能包含所选回测期间或其后的信息。</span><span v-if="profileOverlapsBacktest(profiles[profiles.length - 1])" class="profile-leakage-warning" role="alert">该画像日期与测试期重叠或更晚，存在信息泄漏风险，请勿据此选择方向。</span><IcTrendChart v-if="profiles.length" :points="icPoints" :metric="icMetric" /></template></div></div>
      </aside>
    </div>

    <section class="backtest-launcher" aria-label="因子选股回测">
      <div class="panel-heading">
        <div>
          <h2><Play :size="16" />因子选股回测</h2>
          <span>用上方选中的因子发起研究回测（T+1 执行、A 股真实成本、只读研究）</span>
        </div>
      </div>
      <form class="backtest-form" @submit.prevent="runBacktest">
        <label>回测名称<input v-model="backtestForm.name" required maxlength="100" /></label>
        <label>开始日期<input v-model="backtestForm.start_date" type="date" required /></label>
        <label>结束日期<input v-model="backtestForm.end_date" type="date" required /></label>
        <label>初始资金<input v-model="backtestForm.initial_cash" inputmode="decimal" required /></label>
        <label>持仓数量<input v-model="backtestForm.top_n" inputmode="numeric" required /></label>
        <label>调仓规则
          <select v-model="backtestForm.rebalance_rule">
            <option value="daily">每日</option>
            <option value="weekly">每周（最后交易日）</option>
            <option value="monthly">每月（最后交易日）</option>
            <option value="every_n_trading_days">每 N 个交易日</option>
          </select>
        </label>
        <label v-if="backtestForm.rebalance_rule === 'every_n_trading_days'">
          调仓间隔（交易日）<input v-model="backtestForm.rebalance_interval" inputmode="numeric" />
        </label>
        <label>基准（可选）<input v-model="backtestForm.benchmark" placeholder="如 000300.SH" /></label>
      </form>
      <div class="constraint-row">
        <label class="constraint-toggle">
          <input v-model="backtestForm.useConstraints" type="checkbox" />
          启用组合约束（与模拟盘同源：单标 / 行业 / 现金留存）
        </label>
        <template v-if="backtestForm.useConstraints">
          <label>单标的上限<input v-model="backtestForm.max_symbol_weight" inputmode="decimal" /></label>
          <label>行业上限<input v-model="backtestForm.max_industry_weight" inputmode="decimal" /></label>
          <label>现金留存率<input v-model="backtestForm.cash_reserve_rate" inputmode="decimal" /></label>
        </template>
      </div>
      <div class="preflight-panel">
        <button class="secondary-action" type="button" :disabled="preflightLoading || isRunningBacktest" @click="checkBacktestReadiness">
          {{ preflightLoading ? "正在检查…" : "运行前检查" }}
        </button>
        <p v-if="preflightError" role="alert" class="inline-error">{{ preflightError }}</p>
        <template v-if="preflight">
          <strong>{{ preflight.runnable_now ? "当前输入可尝试运行" : "发现运行阻断" }}</strong>
          <p>检查于 {{ preflight.checked_at }}；正式运行会重新读取并校验数据。{{ preflight.evidence_qualified ? "证据已认证" : "此检查不授予收益认证" }}。</p>
          <ul><li v-for="item in preflight.checks" :key="item.code"><b>{{ item.status === "passed" ? "通过" : item.status === "blocked" ? "阻断" : "限制" }}</b> · {{ item.detail }}<span v-if="item.next_action">；下一步：{{ item.next_action }}</span></li></ul>
        </template>
      </div>
      <p v-if="backtestError" class="inline-error" role="alert">{{ backtestError }}</p>
      <button class="primary-action backtest-run" type="button" :disabled="isRunningBacktest" @click="runBacktest">
        <Play :size="16" />{{ isRunningBacktest ? "回测运行中…（约需数十秒）" : "发起回测" }}
      </button>

      <div v-if="backtestResult" class="backtest-result">
        <div class="result-metrics">
          <div><span>run ID</span><strong>#{{ backtestResult.run_id }}</strong></div>
          <div><span>净收益</span><strong>{{ (Number(backtestResult.total_return) * 100).toFixed(2) }}%</strong></div>
          <div><span>期末权益</span><strong>{{ Number(backtestResult.final_equity).toLocaleString("zh-CN", { maximumFractionDigits: 0 }) }}</strong></div>
          <div><span>成交笔数</span><strong>{{ backtestResult.trade_count }}</strong></div>
        </div>
        <div class="result-assumptions">
          <strong>本次提交的因子方向</strong>
          <ul>
            <li v-for="factor in backtestFactorSnapshot" :key="`${factor.factor_name}@${factor.factor_version}`">
              <span>{{ factor.display_name }}</span>
              <code>{{ factor.factor_name }} · {{ factor.factor_version }}</code>
              <b>{{ factor.higher_is_better ? "↑ 高值优先" : "↓ 低值优先" }}</b>
            </li>
          </ul>
          <p>方向来源：人工预设；因子等权。当前为探索性单次回测（validation_mode=none），未经过训练窗自动选参或样本外验证，不构成收益认证。</p>
        </div>
        <div class="result-actions">
          <RouterLink :to="`/backtests/runs/${backtestResult.run_id}`" class="secondary-action">查看完整回测详情</RouterLink>
          <button class="secondary-action" type="button" :disabled="isLoadingHoldings" @click="loadBacktestHoldings">
            {{ isLoadingHoldings ? "读取中…" : "查看最终持仓" }}
          </button>
          <span class="result-hint">可在「组合草案」页用 run ID {{ backtestResult.run_id }} 一键预填方案。</span>
        </div>
        <div v-if="backtestHoldings && backtestHoldings.holdings.length" class="table-panel full">
          <table>
            <thead><tr><th>代码</th><th>名称</th><th>行业</th><th>数量</th><th>收盘价</th><th>市值</th><th>权重</th></tr></thead>
            <tbody>
              <tr v-for="row in backtestHoldings.holdings" :key="row.symbol">
                <td>{{ row.symbol }}</td>
                <td>{{ row.name ?? "-" }}</td>
                <td>{{ row.industry ?? "-" }}</td>
                <td>{{ row.quantity }}</td>
                <td>{{ Number(row.close).toFixed(2) }}</td>
                <td>{{ Number(row.market_value).toLocaleString("zh-CN", { maximumFractionDigits: 0 }) }}</td>
                <td>{{ (Number(row.weight) * 100).toFixed(2) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="backtestHoldings && !backtestHoldings.holdings.length" class="empty-panel">该回测最末日没有持仓快照。</p>
      </div>
    </section>
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
.category-tag { color: #2563eb; font-weight: 700; }
.factor-selection { padding: 18px; }
.selection-list { display: grid; gap: 8px; padding: 0; list-style: none; }
.selection-list li { padding: 10px 0; border-bottom: 1px solid #eef1f5; }
.selection-list li.direction-missing { margin: 0 -8px; padding: 10px 8px; border: 1px solid #f2b8b5; border-radius: 7px; background: #fff7f6; }
.selected-factor-copy { flex: 1; min-width: 150px; }
.direction-guidance { margin: 12px 0 4px; color: #667085; font-size: 12px; line-height: 1.55; }
.direction-field { display: grid; gap: 4px; color: #667085; font-size: 11px; font-weight: 700; }
.direction-field select { min-width: 150px; min-height: 34px; }
.direction-field select[aria-invalid="true"] { border-color: #c43d3d; }
.icon-button { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid #d7dde6; border-radius: 6px; background: #fff; cursor: pointer; }
.factor-detail { margin-top: 18px; padding-top: 18px; border-top: 1px solid #e3e7ee; }
.factor-detail dl { display: grid; gap: 8px; }
.factor-detail dl div { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font-size: 12px; }
.factor-detail dt { color: #667085; }.factor-detail dd { margin: 0; }
.parameter-list { display: grid; gap: 10px; margin-top: 18px; }
.profile-summary { display: grid; gap: 5px; margin-top: 18px; padding: 12px; border-radius: 7px; background: #f8fafc; font-size: 12px; }
.profile-caveat { color: #667085; line-height: 1.55; }
.profile-leakage-warning { color: #b42318; font-weight: 700; line-height: 1.55; }
.empty-panel { padding: 24px; color: #667085; text-align: center; }
@media (max-width: 900px) { .factor-page { padding: 24px 16px 48px; }.factor-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .page-heading, .factor-toolbar { align-items: stretch; flex-direction: column; }.factor-catalog { grid-template-columns: 1fr; }.selection-list li { align-items: stretch; flex-wrap: wrap; }.direction-field { width: 100%; }.direction-field select { width: 100%; } }
</style>
<style scoped>
.backtest-launcher {
  margin-top: 20px;
  border: 1px solid #e3e7ee;
  border-radius: 8px;
  padding: 18px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-heading h2 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 2px;
  font-size: 16px;
}

.panel-heading span {
  color: #667085;
  font-size: 12px;
}

.backtest-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}

.backtest-form label,
.constraint-row label {
  display: grid;
  gap: 5px;
  color: #667085;
  font-size: 12px;
  font-weight: 700;
}

.constraint-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
}

.constraint-toggle {
  display: flex !important;
  align-items: center;
  gap: 6px;
  grid-auto-flow: column;
}

.constraint-row label:not(.constraint-toggle) input {
  width: 110px;
}

.backtest-run {
  align-self: flex-start;
}

.result-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.result-metrics > div {
  border: 1px solid #eef1f5;
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.result-metrics span {
  font-size: 11px;
  color: #667085;
}

.result-metrics strong {
  font-size: 15px;
}

.result-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.result-assumptions {
  display: grid;
  gap: 8px;
  border: 1px solid #dbe5f3;
  border-radius: 8px;
  padding: 12px;
  background: #f8fbff;
}

.result-assumptions > strong {
  font-size: 13px;
}

.result-assumptions ul {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.result-assumptions li {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) minmax(150px, 1fr) auto;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.result-assumptions code,
.result-assumptions p {
  color: #667085;
  font-size: 11px;
}

.result-assumptions p {
  margin: 0;
  line-height: 1.55;
}

@media (max-width: 620px) {
  .result-assumptions li { grid-template-columns: 1fr; gap: 2px; }
}

.result-hint {
  color: #667085;
  font-size: 12px;
}
</style>
