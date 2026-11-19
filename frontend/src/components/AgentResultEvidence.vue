<script setup lang="ts">
import { computed } from "vue";
import { evidenceUnavailable, factLabels, factValue, resultEvidence, sourceLink } from "../agentEvidence";

const props = defineProps<{ summary: Record<string, unknown> }>();
const evidence = computed(() => resultEvidence(props.summary));
const link = computed(() => evidence.value ? sourceLink(evidence.value) : null);
</script>

<template>
  <div v-if="evidence" class="result-evidence" aria-label="原服务结果事实">
    <strong>原服务结果事实</strong>
    <p v-if="link"><RouterLink :to="link.path">{{ link.label }} ↗</RouterLink></p>
    <p class="muted">观察于 {{ new Date(evidence.observed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }}（上海时间）；刷新页面保留原观察，实时情况以原页面为准。</p>
    <dl><template v-for="(value, key) in evidence.facts" :key="key"><dt>{{ factLabels[key] ?? key }}</dt><dd>{{ factValue(value) }}</dd></template></dl>
    <p v-if="evidence.unavailable_sections.length" role="status">尚不可用的结果：{{ evidence.unavailable_sections.join('、') }}</p>
    <ul><li v-for="limit in evidence.limitations" :key="limit">{{ limit }}</li></ul>
  </div>
  <p v-else-if="evidenceUnavailable(summary)" role="status">原任务身份已核验，业务结果读取暂不可用。请查看原任务和产物，不据此认定研究目标完成。</p>
</template>

<style scoped>
.result-evidence { margin: 10px 0; padding: 12px; background: white; border: 1px solid #dfe5ef; border-radius: 8px; font-size: 12px; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(0, 1fr); gap: 6px 12px; } dt { color: #6d7a90; } dd { margin: 0; font-variant-numeric: tabular-nums; } p, li { line-height: 1.8; } ul { padding-left: 18px; } .muted { color: #6d7a90; }
</style>
