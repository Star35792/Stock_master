<script setup lang="ts">
import { computed } from "vue";
import type { AgentPlan, AgentExecution } from "../agentApi";
import AgentRecoveryEvidence from "./AgentRecoveryEvidence.vue";
import AgentResultEvidence from "./AgentResultEvidence.vue";

const props = defineProps<{ plans: AgentPlan[]; executions: AgentExecution[]; busy: boolean; executionBusy: boolean; toolTitles: Record<string, string> }>();
const emit = defineEmits<{ decision: [planId: string, decision: "approve" | "reject"]; control: [planId: string, operation: "advance" | "refresh" | "cancel"]; inspect: [planId: string, stepId: string]; explain: [version: number] }>();
const plansNewestFirst = computed(() => [...props.plans].reverse());
const labels: Record<string, string> = { needs_input: "待补充参数", draft: "待审阅", approved: "计划已批准", rejected: "已拒绝", superseded: "已被新需求替代", expired: "已过期或代码/技能已变化" };
const intents: Record<string, string> = { data_maintenance: "数据维护", stock_research: "选股研究", position_analysis: "持仓分析", portfolio_planning: "组合规划", consultation: "解释咨询" };
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const executionFor = (id: string) => props.executions.find(item => item.plan_id === id);
const executionLabels: Record<string, string> = { ready: "待继续", running: "处理中 / 结果待核实", awaiting_confirmation: "等待逐项确认", waiting_task: "等待任务", completed: "步骤完成 · 目标待核验", blocked: "已阻断", cancelled: "已停止后续步骤", pending: "待运行", reading: "读取中 / 待核实", preflighting: "预检中 / 待核实", starting: "任务创建中 / 待核实", waiting_read: "等待原任务", success: "步骤调用完成", partial: "部分失败", failed: "失败", unknown: "结果未知" };
</script>

<template>
  <section class="plan-panel" aria-label="可审阅计划">
    <h2>研究计划 <span>{{ plans.length }}/4</span></h2>
    <p class="muted">批准只保存计划。点击运行后按依赖执行只读步骤；写步骤必须另行预检并逐项确认。任务成功不等于研究目标或证据资格已达成。</p>
    <p v-if="!plans.length" class="muted">使用“先规划”提交需求，查看目标、依赖、参数和完成条件。</p>
    <article v-for="plan in plansNewestFirst" :key="plan.plan_id" class="plan-card">
      <h3>第 {{ plan.version }} 版 · {{ intents[plan.intent] }} <span>{{ labels[plan.status] }}</span></h3>
      <p class="goal">{{ plan.goal }}</p>
      <p v-for="skill in plan.skills ?? []" :key="skill.name" class="muted">专业技能：{{ skill.name }} · {{ skill.content_hash.slice(0, 12) }}</p>
      <div v-if="plan.status === 'needs_input'" class="questions" role="status">
        <strong>请补充后生成新版本</strong>
        <p>日期请用 YYYY-MM-DD，预算请用数字金额（如 1000000）；因子方向请明确 true / false。</p>
        <ul><li v-for="question in plan.clarification_questions" :key="question">{{ question }}</li><li v-for="missing in plan.missing_inputs" :key="missing">{{ missing }}</li></ul>
      </div>
      <details :open="['draft', 'needs_input', 'approved'].includes(plan.status)">
        <summary>目标、参数与依赖</summary>
        <p><strong>完成条件：</strong>{{ plan.completion_condition }}</p>
        <p><strong>拟读取来源（待查询）：</strong>{{ plan.data_sources.join('；') }}</p>
        <ul v-if="plan.assumptions.length"><li v-for="assumption in plan.assumptions" :key="assumption">假设：{{ assumption }}</li></ul>
        <p v-if="!plan.steps.length" class="muted">{{ plan.intent === 'consultation' ? '解释咨询，无执行步骤；需要事实查询时可选择“查询与解释”。' : '补齐信息后再生成具体步骤。' }}</p>
        <ol class="steps">
          <li v-for="step in plan.steps" :key="step.step_id">
            <strong>{{ toolTitles[step.tool] ?? step.tool }} · {{ step.step_id }}</strong>
            <p>{{ step.purpose }}</p>
            <p class="muted">依赖：{{ step.depends_on.join('、') || '无' }} · 完成条件：{{ step.completion_condition }}</p>
            <pre>{{ pretty(step.parameters) }}</pre>
            <p v-for="binding in step.bindings" :key="binding.parameter" class="binding">{{ binding.parameter }}：待上游 {{ binding.source_step }} 的 {{ binding.output }} 结果绑定，当前无已核实值。</p>
            <details v-if="Object.keys(step.input_evidence).length"><summary>参数对应的用户原话</summary><pre>{{ pretty(step.input_evidence) }}</pre></details>
          </li>
        </ol>
        <p class="muted">有效期至 {{ new Date(plan.expires_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }}（上海时间）</p>
        <details><summary>版本身份</summary><pre>{{ plan.plan_hash }}
代码版本：{{ plan.code_version }}</pre></details>
      </details>
      <div v-if="['draft', 'needs_input'].includes(plan.status)" class="buttons">
        <button :disabled="busy || plan.status !== 'draft'" @click="emit('decision', plan.plan_id, 'approve')">批准计划（不会执行）</button>
        <button :disabled="busy" class="secondary" @click="emit('decision', plan.plan_id, 'reject')">拒绝此版</button>
      </div>
      <div v-if="executionFor(plan.plan_id)" class="execution" aria-live="polite">
        <h4>{{ executionLabels[executionFor(plan.plan_id)!.status] }}</h4>
        <p>{{ executionFor(plan.plan_id)!.reason }}</p>
        <ul>
          <li v-for="step in executionFor(plan.plan_id)!.steps" :key="step.step_id">
            <strong>{{ step.step_id }} · {{ executionLabels[step.status] ?? step.status }}</strong>
            <p v-if="step.task_id">原任务 #{{ step.task_id }} · <RouterLink to="/operations">查看任务详情 ↗</RouterLink></p>
            <p v-if="step.reason">{{ step.reason }}</p>
            <AgentRecoveryEvidence :step="step" :busy="busy" @inspect="emit('inspect', plan.plan_id, $event)" />
            <AgentResultEvidence :summary="step.summary" />
            <details v-if="Object.keys(step.summary).length"><summary>结果摘要与来源</summary><pre>{{ pretty(step.summary) }}</pre></details>
          </li>
        </ul>
        <p class="muted">取消只停止后续步骤，已开始的任务可能继续；未知结果不会自动重放。</p>
        <button class="secondary" :disabled="busy" @click="emit('explain', plan.version)">解释已保存结果与限制</button>
      </div>
      <div class="buttons">
        <button v-if="plan.status === 'approved' && (!executionFor(plan.plan_id) || ['ready', 'waiting_task'].includes(executionFor(plan.plan_id)!.status))" :disabled="busy" @click="emit('control', plan.plan_id, 'advance')">运行 / 继续计划</button>
        <button v-if="executionFor(plan.plan_id)?.steps.some(step => step.task_id)" class="secondary" :disabled="busy" @click="emit('control', plan.plan_id, 'refresh')">刷新原任务状态</button>
        <button v-if="plan.status === 'approved' || executionFor(plan.plan_id)" class="secondary" :disabled="busy && !executionBusy" @click="emit('control', plan.plan_id, 'cancel')">停止后续步骤</button>
      </div>
    </article>
  </section>
</template>

<style scoped>
.plan-panel { padding: 20px; border: 1px solid #dfe5ef; background: white; border-radius: 12px; min-width: 0; }
h2 { font-size: 15px; display: flex; justify-content: space-between; margin: 0 0 14px; } h2 span, .muted { color: #6d7a90; }
.plan-card { border-top: 1px solid #e7ecf5; padding: 16px 0 4px; } h3 { font-size: 14px; margin: 0 0 10px; display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; } h3 span { font-size: 11px; color: #566b9c; }
.execution { padding: 12px; margin-top: 12px; background: #f3f6ff; border-radius: 8px; }.execution h4 { margin: 0; font-size: 13px; }
p, li { font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; } .goal { font-weight: 600; } .questions { padding: 12px; background: #fff8e9; border-radius: 8px; font-size: 12px; } .questions p { margin: 6px 0; } ul, ol { padding-left: 20px; }
.steps > li { padding: 8px 0; } pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 220px; overflow: auto; background: #f7f9fc; padding: 10px; border-radius: 6px; font-size: 11px; line-height: 1.7; }
summary { cursor: pointer; padding: 8px 0; color: #576987; font-size: 12px; } .binding { color: #875e20; } .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; } button { cursor: pointer; color: white; background: #4663d4; border: 1px solid #4663d4; border-radius: 7px; padding: 9px 12px; font-size: 12px; } .secondary { background: white; color: #52617a; border-color: #dce2ec; } button:disabled { opacity: .5; cursor: default; } button:focus-visible, summary:focus-visible { outline: 2px solid #4663d4; outline-offset: 3px; }
</style>
