<script setup lang="ts">
import { onBeforeUnmount, watch } from "vue";
import { createTrainingDiagnostics } from "../../composables/trainingDiagnostics";

const props = defineProps<{ runId: number }>();
const { report, loading, error, reset, load } = createTrainingDiagnostics();
watch(() => props.runId, reset, { immediate: true });
onBeforeUnmount(() => reset(0));

const statusText = {
  recorded: "训练证据已记录",
  insufficient: "证据不足",
  not_recorded: "旧产物未记录",
  unsupported: "产物合同不支持",
};
const reasonText: Record<string, string> = {
  run_has_no_training_artifact: "该运行没有绑定训练产物。",
  insufficient_artifact_evidence: "产物没有逐日 RankIC 证据；不会用当前数据回填历史。",
  training_artifact_missing: "绑定的训练产物不存在。",
  training_artifact_snapshot_too_large: "训练产物超过只读诊断上限。",
  invalid_training_diagnostics_contract: "训练诊断合同无效。",
};

function decimal(value: string | null, digits = 4): string {
  if (value === null) return "不可计算";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "不可计算";
}
</script>

<template>
  <section class="training-diagnostics" aria-labelledby="training-diagnostics-title" :aria-busy="loading">
    <div class="heading">
      <div>
        <h2 id="training-diagnostics-title">训练窗统计诊断</h2>
        <p class="muted">逐日 RankIC 的 lag-1 HAC 双侧检验与同窗声明候选族 BH 校正。</p>
      </div>
      <button type="button" :disabled="loading" @click="load">
        {{ loading ? '正在读取…' : report ? '重新读取' : '查看诊断' }}
      </button>
    </div>
    <p class="muted">按需读取不可变训练产物；不会重跑、补算旧数据或改变选因子与研究资格。</p>
    <p v-if="loading" role="status">正在读取训练产物中的已记录证据…</p>
    <p v-if="error" class="warning" role="alert">{{ error }}</p>
    <div v-if="report" aria-live="polite">
      <p class="result" :class="report.status">
        <strong>{{ statusText[report.status] }}</strong>
        <span v-if="report.reason"> · {{ reasonText[report.reason] || report.reason }}</span>
      </p>
      <template v-if="report.factors.length">
        <p>
          候选 {{ report.family.declared_count }} 个 · 可检验 {{ report.family.testable_count }} 个 ·
          不可检验 {{ report.family.unavailable_count }} 个 · FDR α={{ report.family.alpha }}
        </p>
        <div class="scroll">
          <table>
            <caption>数值仅诊断该训练窗，不按 p/q 值排序或选择冠军。</caption>
            <thead><tr><th>因子</th><th>有效日</th><th>Mean RankIC</th><th>HAC SE</th><th>p</th><th>q</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="factor in report.factors" :key="`${factor.factor_name}@${factor.factor_version}`">
                <td>{{ factor.factor_name }}@{{ factor.factor_version }}</td>
                <td>{{ factor.observations }}</td>
                <td>{{ decimal(factor.mean_rank_ic) }}</td>
                <td>{{ decimal(factor.hac_standard_error) }}</td>
                <td>{{ decimal(factor.p_value) }}</td>
                <td>{{ decimal(factor.q_value) }}</td>
                <td>{{ factor.status === 'recorded' ? (factor.reject_null ? '训练窗拒绝零假设' : '未拒绝零假设') : factor.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <p class="warning"><strong>p/q 值不是未来盈利概率，也不是策略晋级或执行证据。</strong></p>
      <details v-if="report.contract_version">
        <summary>证据身份与边界</summary>
        <p>合同：{{ report.contract_version }} · 训练产物 #{{ report.training_artifact_id }} · {{ report.artifact_hash }}</p>
        <p>逐日证据：{{ report.evidence_sha256 }} · 日期数 {{ report.eligible_dates.length }}</p>
        <p>价格摘要：{{ report.price_snapshot.sha256 || '未记录' }}（{{ report.price_snapshot.row_count || 0 }} 行）</p>
        <ul><li v-for="item in report.declarations" :key="item">{{ item }}</li></ul>
      </details>
    </div>
  </section>
</template>

<style scoped>
.training-diagnostics{background:white;border:1px solid #dce3ed;border-radius:10px;padding:20px;margin:18px 0;overflow-wrap:anywhere}.heading{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}h2{margin:0 0 10px;font-size:20px}.muted{color:#667085;font-size:13px}button{padding:8px 12px;border:1px solid #b8c5d6;border-radius:5px;background:white;cursor:pointer}button:disabled{opacity:.5;cursor:default}.result{padding:12px;border-left:3px solid #237c60;background:#f1f5f9}.insufficient,.not_recorded,.unsupported,.warning{color:#a34700}.insufficient,.not_recorded,.unsupported{border-color:#a34700}.scroll{overflow-x:auto;margin:16px 0}table{border-collapse:collapse;width:100%;min-width:760px;font-size:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid #e5e7eb}caption{text-align:left;color:#667085;font-size:13px;margin-bottom:8px}details{font-size:13px;margin-top:16px}summary{cursor:pointer}li{margin:7px 0}
</style>
