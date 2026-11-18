<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { createSensitivity } from "../../composables/sensitivity";
import { sensitivityAxes, type SensitivityCell } from "../../sensitivityApi";
const props = defineProps<{ selectedRunIds: number[] }>();
const { input, report, loading, error, useSelection, calculate, dispose } = createSensitivity();
const fold = ref("");
const activeFold = computed(() => report.value?.folds[fold.value] ? fold.value : Object.keys(report.value?.folds ?? {})[0]);
const cells = computed(() => report.value?.cells.filter(cell => cell.fold === activeFold.value) ?? []);
const labels = { controlled: "合同一致", exploratory: "探索性", missing: "缺失", duplicate: "重复", unavailable: "不可用" };
const percent = (value: string | null) => value === null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
const dimension = (index: number) => [...new Set(Object.values(report.value?.candidates ?? {}).map(v => v[index] ?? "未设置"))];
function gridCell(x: string, y: string) {
  return cells.value.find(cell => (cell.axis_values[0] ?? "未设置") === x && (cell.axis_values[1] ?? "未设置") === y);
}
function shade(cell?: SensitivityCell) {
  if (cell?.status !== "controlled" || cell.total_return === null) return {};
  const opacity = Math.min(.55, .1 + Math.abs(Number(cell.total_return)));
  return { background: Number(cell.total_return) >= 0 ? `rgba(16,125,90,${opacity})` : `rgba(190,64,64,${opacity})` };
}
const points = computed(() => {
  if (report.value?.request.axes.length !== 1) return [];
  const numeric = cells.value.map((cell, index) => ({ cell, index }));
  const valid = numeric.filter(({ cell }) => cell.status === "controlled" && cell.total_return !== null);
  const values = valid.map(({ cell }) => Number(cell.total_return));
  const low = Math.min(0, ...values), high = Math.max(0, ...values);
  return valid.map(({ cell, index }) => ({ id: cell.candidate, label: cell.axis_values[0] ?? "未设置",
    value: percent(cell.total_return), x: 40 + index * 520 / Math.max(1, cells.value.length - 1),
    y: high === low ? 90 : 150 - (Number(cell.total_return) - low) * 120 / (high - low) }));
});
function download() {
  if (!report.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(report.value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = `sensitivity-${report.value.contract_sha256.slice(0, 12)}.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
onBeforeUnmount(dispose);
</script>

<template>
  <details class="sensitivity-panel">
    <summary>参数敏感性比较 <small>既有运行 · 事后研究记录</small></summary>
    <p>比较单轴或双轴参数在各窗口的已保存绩效。缺失、失败和重复运行保留，不自动选冠军或补跑。</p>
    <button type="button" :disabled="loading || selectedRunIds.length < 2" @click="useSelection(props.selectedRunIds)">载入当前勾选（首项为基准）</button>
    <form @submit.prevent="calculate">
      <fieldset :disabled="loading"><legend>实验范围</legend><div class="fields">
        <label class="ids">运行编号（可包含失败运行）<input v-model="input.ids" placeholder="1,2,3,4" required maxlength="1400"></label>
        <label>基准运行编号<input v-model="input.baseline" type="number" min="1" step="1" required></label>
        <label>第一扫描轴<select v-model="input.axis"><option v-for="(name, key) in sensitivityAxes" :key="key" :value="key">{{ name }}</option></select></label>
        <label>第二扫描轴<select v-model="input.secondAxis"><option value="">无（单轴）</option><option v-for="(name, key) in sensitivityAxes" :key="key" :value="key">{{ name }}</option></select></label>
        <label>研究假设<textarea v-model="input.hypothesis" maxlength="1000" rows="2" /></label>
        <label>接受说明<textarea v-model="input.acceptance" maxlength="1000" rows="2" /></label>
        <label>停止说明<textarea v-model="input.stop" maxlength="1000" rows="2" /></label>
      </div></fieldset>
      <p class="muted">这些说明于本次比较时记录，不能代替历史预注册。只核对记录中的合同与输入摘要，不认证完整历史时点或收益资格。</p>
      <button type="submit" :disabled="loading">{{ loading ? '正在比较…' : '生成敏感性报告' }}</button>
    </form>
    <p v-if="error" role="alert" class="notice">{{ error }}</p>
    <div v-if="report" aria-live="polite">
      <p>基准候选 {{ report.baseline_candidate ?? '不可识别' }} · {{ Object.keys(report.folds).length }} 个窗口 · {{ Object.keys(report.candidates).length }} 个候选 <button type="button" @click="download">导出研究记录 JSON</button></p>
      <p v-if="report.unclassified_run_ids.length" role="status" class="notice">未归类运行：<span v-for="id in report.unclassified_run_ids" :key="id">#{{ id }} {{ report.unclassified_reasons[id] }} </span></p>
      <label>展示窗口<select :value="activeFold" @change="fold = ($event.target as HTMLSelectElement).value"><option v-for="(dates, key) in report.folds" :key="key" :value="key">{{ key }} · {{ dates.join(' 至 ') }}</option></select></label>
      <figure v-if="points.length"><svg viewBox="0 0 600 195" role="img" aria-label="单轴已保存收益散点图"><line x1="40" y1="160" x2="560" y2="160" stroke="#cbd5e1" /><g v-for="point in points" :key="point.id"><circle :cx="point.x" :cy="point.y" r="5" fill="#1d4ed8"><title>{{ point.label }}：{{ point.value }}</title></circle><text :x="point.x" :y="point.y - 12" text-anchor="middle">{{ point.value }}</text><text :x="point.x" y="180" text-anchor="middle">{{ point.label }}</text></g></svg><figcaption>横轴为按参数值排序的候选，等距展示；纵向为总收益。仅绘制合同一致的实测点，缺格不连线。</figcaption></figure>
      <div v-if="report.request.axes.length === 2" class="scroll"><table class="heatmap"><caption>双轴实测收益 · 行 {{ sensitivityAxes[report.request.axes[1]!] }} / 列 {{ sensitivityAxes[report.request.axes[0]!] }}</caption><thead><tr><th>参数值</th><th v-for="x in dimension(0)" :key="x">{{ x }}</th></tr></thead><tbody><tr v-for="y in dimension(1)" :key="y"><th>{{ y }}</th><td v-for="x in dimension(0)" :key="x" :style="shade(gridCell(x, y))">{{ percent(gridCell(x, y)?.total_return ?? null) }}<small>{{ gridCell(x, y) ? labels[gridCell(x, y)!.status] : '缺失' }}</small></td></tr></tbody></table></div>
      <div class="scroll"><table><caption>候选 × 窗口明细（{{ activeFold }}）</caption><thead><tr><th>候选 / 扫描值</th><th>运行</th><th>状态</th><th>总收益</th><th>相对基准差值</th><th>最大回撤</th><th>夏普</th><th>原因 / 固定合同差异</th></tr></thead><tbody><tr v-for="cell in cells" :key="cell.candidate"><td>{{ cell.candidate }} · {{ cell.axis_values.map(v => v ?? '未设置').join(' / ') }}</td><td>{{ cell.run_ids.map(id => `#${id}`).join(', ') || '—' }}</td><td>{{ labels[cell.status] }}</td><td>{{ percent(cell.total_return) }}</td><td>{{ cell.delta_return === null ? '—' : `${(Number(cell.delta_return) * 100).toFixed(2)} 个百分点` }}</td><td>{{ percent(cell.maximum_drawdown) }}</td><td>{{ cell.sharpe_ratio ?? '—' }}</td><td>{{ [...cell.reasons, ...cell.contract_differences].join('；') || '记录合同一致' }}</td></tr></tbody></table></div>
      <div class="scroll"><table><caption>跨窗口描述统计 · 不复利拼接，不排名</caption><thead><tr><th>候选</th><th>完整性</th><th>合同一致窗口</th><th>中位收益</th><th>最差收益</th><th>收益范围</th></tr></thead><tbody><tr v-for="item in report.summaries" :key="item.candidate"><td>{{ item.candidate }}</td><td>{{ item.complete ? '完整' : '不完整 / 不可排名' }}</td><td>{{ item.controlled_folds }}/{{ item.expected_folds }}</td><td>{{ percent(item.median_return) }}</td><td>{{ percent(item.worst_return) }}</td><td>{{ percent(item.return_range) }}</td></tr></tbody></table></div>
      <details><summary>比较口径与摘要</summary><ul><li v-for="item in report.declarations" :key="item">{{ item }}</li></ul><p>{{ report.version }} · {{ report.contract_sha256 }}</p></details>
    </div>
  </details>
</template>

<style scoped>
.sensitivity-panel{padding:20px;background:#fff;border:1px solid #dce3ed;border-radius:12px;margin:20px 0;overflow-wrap:anywhere}.sensitivity-panel>summary{cursor:pointer;font-size:18px;font-weight:700}small,.muted,figcaption{font-size:12px;color:#667085}summary small{margin-left:10px}p{font-size:14px;line-height:1.6}fieldset{border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px}.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.ids{grid-column:1/-1}label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:#475467}input,select,textarea,button{font:inherit;padding:8px;border:1px solid #cbd5e1;border-radius:5px;background:#fff;min-width:0}button{cursor:pointer;font-size:13px}button:disabled{opacity:.5;cursor:default}.notice{padding:10px;background:#fff7e8;color:#8a4b00}.scroll{overflow:auto;margin:18px 0}table{border-collapse:collapse;min-width:680px;width:100%;font-size:13px}caption{text-align:left;font-weight:600;margin:10px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:top}td small{display:block}.heatmap td{text-align:center}figure{max-width:700px;margin:20px 0}svg{width:100%;font-size:12px}details details{font-size:13px}
</style>
