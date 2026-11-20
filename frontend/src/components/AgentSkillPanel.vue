<script setup lang="ts">
import type { AgentSkillVerification } from "../agentApi";

defineProps<{ skills: Array<{ name: string; description: string }>; checks: AgentSkillVerification[]; busy: boolean }>();
const emit = defineEmits<{ select: [name: string] }>();
const titles: Record<string, string> = { "data-quality-diagnosis": "数据质量诊断", "factor-backtest-review": "因子回测解读", "position-risk-review": "持仓风险检查" };
const labels = { ready_to_explain: "检查材料齐备", needs_evidence: "仍需补充证据", needs_input: "需要明确资源" };
</script>

<template>
  <section class="skill-panel" aria-label="专业技能与材料检查">
    <h2>专业技能</h2>
    <p>按需加载检查方法。选择技能后填写研究需求，再按当前处理方式提交；回测和持仓检查需提供对应 ID。</p>
    <button v-for="skill in skills" :key="skill.name" :disabled="busy" @click="emit('select', skill.name)">
      <strong>{{ titles[skill.name] ?? skill.name }}</strong><span>{{ skill.description }}</span>
    </button>
    <article v-for="(check, index) in checks" :key="`${check.skill.name}-${index}`">
      <h3>{{ titles[check.skill.name] ?? check.skill.name }} · {{ labels[check.status] }}</h3>
      <p>检查时间 {{ new Date(check.observed_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }} · 上海时间</p>
      <p>技能版本 {{ check.skill.content_hash.slice(0, 12) }}</p>
      <p v-if="check.missing_checks.length">待补查：{{ check.missing_checks.join('、') }}</p>
      <ul><li v-for="limit in check.limitations" :key="limit">{{ limit }}</li></ul>
      <details><summary>原服务证据与技能身份</summary><pre>{{ JSON.stringify({ skill: check.skill, evidence: check.evidence_refs }, null, 2) }}</pre></details>
    </article>
  </section>
</template>

<style scoped>
.skill-panel { padding: 20px; background: white; border: 1px solid #dfe5ef; border-radius: 12px; }
h2 { margin: 0 0 12px; font-size: 15px; } h3 { font-size: 13px; }
p, li { color: #63718a; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
button { display: grid; gap: 5px; width: 100%; margin: 8px 0; padding: 12px; text-align: left; background: #f6f8fd; border: 1px solid #e0e6f0; border-radius: 8px; color: #42516a; cursor: pointer; }
button span { font-size: 12px; line-height: 1.6; } button:disabled { opacity: .5; cursor: default; }
button:focus-visible, summary:focus-visible { outline: 2px solid #4663d4; outline-offset: 3px; }
article { margin-top: 16px; border-top: 1px solid #edf0f5; } summary { font-size: 12px; cursor: pointer; }
pre { max-height: 260px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; background: #f7f9fc; padding: 10px; }
</style>
