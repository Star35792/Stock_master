<script setup lang="ts">
import { onBeforeUnmount, watch } from "vue";
import { createSignalAudit } from "../../composables/signalAudit";

const props = defineProps<{ runId: number }>();
const { report, loading, error, note, reset, start, stopWaiting } = createSignalAudit();
watch(() => props.runId, reset, { immediate: true });
onBeforeUnmount(() => reset(0));
const statuses = { passed: "抽样通过", failed: "发现差异", insufficient: "不可核验", error: "计算异常" };
const kinds: Record<string, string> = {
  repeat_before: "计算前重复", repeat_after: "计算后重复", truncate: "截断未来", perturb: "扰动未来",
};
</script>

<template>
  <section class="signal-audit" aria-labelledby="signal-audit-title" :aria-busy="loading">
    <div class="audit-heading">
      <div><h2 id="signal-audit-title">信号行为审计</h2><p class="muted">改变未来因子输入，检查过去目标权重信号是否变化。</p></div>
      <div class="audit-actions">
        <button type="button" :disabled="loading" @click="start">{{ loading ? '正在审计…' : report ? '重新审计' : '开始审计' }}</button>
        <button v-if="loading" type="button" @click="stopWaiting">停止等待</button>
      </div>
    </div>
    <p class="muted">核验范围：因子输入 → 信号。抽样通过仍需复核数据时点与回测假设。报告仅在本页展示。</p>
    <p v-if="loading" role="status">正在核对运行证据并重算，最多抽查 3 个截止点。停止等待后服务端可能继续计算。</p>
    <p v-if="note" role="status">{{ note }}</p>
    <p v-if="error" class="warning" role="alert">{{ error }}</p>
    <div v-if="report" aria-live="polite">
      <p class="audit-result" :class="report.status"><strong>{{ statuses[report.status] }}</strong> · {{ report.reason }}</p>
      <dl class="audit-metrics">
        <div><dt>本次载入因子</dt><dd>{{ report.input_rows.toLocaleString('zh-CN') }} 行</dd></div>
        <div><dt>本次重算信号</dt><dd>{{ report.signal_count.toLocaleString('zh-CN') }} 条</dd></div>
        <div><dt>重算次数</dt><dd>{{ report.evaluations }} 次</dd></div>
        <div><dt>原运行核对</dt><dd>{{ report.baseline_matches_run ? '完整信号一致' : '尚未确认一致' }}</dd></div>
      </dl>
      <div v-if="report.checks.length" class="audit-scroll">
        <table><caption>抽样检查明细</caption><thead><tr><th>检查</th><th>截止日期</th><th>改动输入行</th><th>比较信号</th><th>差异</th></tr></thead>
          <tbody><tr v-for="check in report.checks" :key="`${check.kind}-${check.cutoff}`"><td>{{ kinds[check.kind] || check.kind }}</td><td>{{ check.cutoff }}</td><td>{{ check.changed_input_rows }}</td><td>{{ check.compared_values }}</td><td>{{ check.differences }}</td></tr></tbody>
        </table>
      </div>
      <template v-for="check in report.checks" :key="`examples-${check.kind}-${check.cutoff}`">
        <div v-if="check.examples.length" class="audit-scroll">
          <table><caption>{{ kinds[check.kind] }} · {{ check.cutoff }} 差异示例（缺失与零退出分别比较）</caption><thead><tr><th>信号日</th><th>证券</th><th>原始权重</th><th>重算权重</th></tr></thead><tbody><tr v-for="item in check.examples" :key="`${item.signal_date}-${item.symbol}`"><td>{{ item.signal_date }}</td><td>{{ item.symbol }}</td><td>{{ item.expected ?? '无信号' }}</td><td>{{ item.actual ?? '无信号' }}</td></tr></tbody></table>
        </div>
      </template>
      <details><summary>证据与核验边界</summary>
        <p>审计时间：{{ new Date(report.generated_at).toLocaleString('zh-CN') }} · {{ report.version }}</p>
        <p>原代码：{{ report.recorded_code_version || '未记录' }}<br>当前代码：{{ report.current_code_version }}</p>
        <p>因子摘要：{{ report.input_sha256 || '未核验' }} · {{ report.input_verified ? '摘要一致' : '未确认' }}</p>
        <ul><li v-for="declaration in report.declarations" :key="declaration">{{ declaration }}</li></ul>
        <p>上限：{{ report.limits.rows }} 行、{{ report.limits.dates }} 个观察日、{{ report.limits.features }} 个因子；协作式计算预算 {{ report.limits.seconds }} 秒。</p>
      </details>
    </div>
  </section>
</template>

<style scoped>
.signal-audit{background:white;border:1px solid #dce3ed;border-radius:10px;padding:20px;margin:18px 0;overflow-wrap:anywhere}.audit-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}h2{margin:0 0 10px;font-size:20px}.muted,dt{color:#667085;font-size:13px}.audit-actions{display:flex;gap:8px;flex-wrap:wrap}button{padding:8px 12px;border:1px solid #b8c5d6;border-radius:5px;background:white;cursor:pointer}button:disabled{opacity:.5;cursor:default}.audit-result{padding:12px;border-left:3px solid #64748b;background:#f1f5f9}.passed{border-color:#237c60}.failed,.error,.warning{color:#a34700}.failed,.error{border-color:#a34700}.audit-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:14px}.audit-metrics dd{margin:5px 0;font-weight:600}.audit-scroll{overflow-x:auto;margin:16px 0}table{border-collapse:collapse;width:100%;min-width:520px;font-size:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid #e5e7eb}caption{text-align:left;color:#667085;font-size:13px;margin-bottom:8px}details{font-size:13px;margin-top:16px}summary{cursor:pointer}li{margin:7px 0}
</style>
