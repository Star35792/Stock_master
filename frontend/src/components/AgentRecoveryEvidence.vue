<script setup lang="ts">
import { computed } from "vue";
import { canInspectOriginalTask, recoveryEvidence, retryDescription, type RecoveryStep } from "../agentRecovery";

const props = defineProps<{ step: RecoveryStep; busy: boolean }>();
const emit = defineEmits<{ inspect: [stepId: string] }>();
const evidence = computed(() => recoveryEvidence(props.step));
const retry = computed(() => retryDescription(props.step));
const labels: Record<string, string> = {
  verified: "原任务与产物身份已核验", running: "原任务仍在运行", partial: "原任务部分完成",
  failed: "原任务失败", unknown: "完成证据待核实", conflict: "任务或产物证据冲突",
};
</script>

<template>
  <div v-if="retry || canInspectOriginalTask(step) || evidence" class="recovery">
    <p v-if="retry" role="status">{{ retry }}</p>
    <button v-if="canInspectOriginalTask(step)" type="button" :disabled="busy" @click="emit('inspect', step.step_id)">核验原任务与产物</button>
    <p v-if="canInspectOriginalTask(step)" class="muted">仅核查原确认关联的记录。已开始的任务可能继续，核验不会重新执行。</p>
    <div v-if="evidence" role="status">
      <strong>{{ labels[evidence.status] ?? '完成证据待核实' }}</strong>
      <p>{{ evidence.reason }}</p>
      <p>确认 ID：{{ evidence.confirmation_id }}<span v-if="evidence.task_id"> · 原任务 #{{ evidence.task_id }}</span></p>
      <p v-if="evidence.artifact_id">
        原{{ evidence.artifact_type === 'backtest' ? '回测' : '组合草案' }} #{{ evidence.artifact_id }} · {{ evidence.artifact_status }}
        <RouterLink v-if="evidence.artifact_type === 'backtest'" :to="`/backtests/runs/${evidence.artifact_id}`">查看原回测</RouterLink>
        <RouterLink v-else to="/portfolio">查看组合草案</RouterLink>
      </p>
      <RouterLink v-if="evidence.task_id" to="/operations">查看运维任务记录</RouterLink>
      <p class="muted">身份核验不等于研究目标达成，也不提升数据或策略资格。未完整成功时请重新规划。</p>
    </div>
  </div>
</template>

<style scoped>
.recovery { margin-top: 10px; padding: 10px; border: 1px solid #dbe3f0; border-radius: 7px; background: #fff; overflow-wrap: anywhere; }
p, a, strong { font-size: 12px; line-height: 1.8; } p { margin: 5px 0; } .muted { color: #65738a; }
a { margin-left: 8px; color: #3657bd; } button { padding: 7px 10px; border: 1px solid #bdcbe8; border-radius: 6px; background: #f6f8ff; color: #3657bd; cursor: pointer; font-size: 12px; }
button:disabled { opacity: .5; cursor: default; } button:focus-visible, a:focus-visible { outline: 2px solid #4663d4; outline-offset: 3px; }
</style>
