<script setup lang="ts">
import { Bot, RefreshCw, Send, Sparkles } from "@lucide/vue";
import { onBeforeUnmount, onMounted, ref } from "vue";

import { createAgentWorkspace } from "../composables/agentWorkspace";
import AgentPlanPanel from "../components/AgentPlanPanel.vue";
import type { AgentMemory } from "../agentApi";

const { session, status, tasks, input, mode, plansNeedReload, busy, executionBusy, loading, error, taskError, sessions, memories, restore, remember, forget, load, send, decide, decidePlan, controlPlan, recoverPlanStep, reset, refreshTasks, dispose } = createAgentWorkspace();
const memoryText = ref("");
const memoryKind = ref<"preference" | "constraint" | "lesson">("preference");
const editingMemory = ref<AgentMemory | null>(null);
function editMemory(memory: AgentMemory) { editingMemory.value = memory; memoryKind.value = memory.kind; memoryText.value = memory.content; }
function clearMemoryForm() { editingMemory.value = null; memoryText.value = ""; }
async function saveMemory() { if (await remember(memoryKind.value, memoryText.value, editingMemory.value ?? undefined)) clearMemoryForm(); }
const examples = [
  "检查数据覆盖和未解决的质量问题，给我下一步处理建议",
  "查询最近的研究回测，解释结果和仍未解决的数据资格限制",
  "帮我规划日线采集、缺口回填、因子研究和组合草案的流程，缺参数先问我",
];
const labels: Record<string, string> = { starting: "创建结果待核实", pending: "等待确认", confirmed: "已确认", cancelled: "已取消", expired: "已过期", queued: "已排队", running: "执行中", success: "成功", partial: "部分完成", failed: "失败" };
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(async () => {
  await load();
  timer = setInterval(() => { if (!busy.value) void refreshTasks(); }, 4000);
});
onBeforeUnmount(() => { clearInterval(timer); dispose(); });
</script>

<template>
  <div class="agent-page">
    <header class="agent-heading">
      <div><p class="agent-eyebrow"><Sparkles :size="15" /> RESEARCH AGENT</p><h1>智能体工作台</h1><p>从一句研究需求开始，串起数据、诊断、回测与组合草案。</p></div>
      <button class="agent-secondary" :disabled="busy" @click="reset">新建对话</button>
    </header>

    <div class="agent-connection" role="status">
      <span class="agent-dot" :class="{ ready: status?.configured }" />
      <strong>{{ loading ? "连接中" : status?.configured ? `${status.model} · 已配置` : "DeepSeek 尚未配置" }}</strong>
      <span>需求与有界对话会发送给 DeepSeek；计划批准与业务执行分开。</span>
      <button :disabled="loading || busy" class="agent-icon" aria-label="重新连接智能体" @click="load"><RefreshCw :size="16" /></button>
    </div>
    <p v-if="!loading && status && !status.configured" class="agent-error">请在后端配置 DEEPSEEK_API_KEY 并重启服务；此页面不会读取或展示密钥。</p>
    <p v-if="error" role="alert" class="agent-error">{{ error }}</p>
    <p class="agent-muted">当前阶段：故障恢复与证据核验。先批准计划，再按范围执行；只读瞬时失败有限重试。写步骤仍需逐项确认，默认关闭；结果未知时核验原任务，不重复执行。</p>
    <label v-if="sessions.length" class="agent-history">恢复历史会话 <select :disabled="busy || loading" :value="session?.session_id ?? ''" @change="restore(($event.target as HTMLSelectElement).value)"><option value="" disabled>选择已保存对话</option><option v-for="item in sessions" :key="item.session_id" :value="item.session_id">{{ item.title }}</option></select></label>

    <div class="agent-grid">
      <section class="agent-conversation" aria-label="研究对话">
        <div v-if="!session?.messages.length" class="agent-welcome">
          <div class="agent-bot"><Bot :size="30" /></div><h2>你希望完成什么研究？</h2>
          <p>先明确目标和缺失参数，再展示供你审阅的计划。需要现有事实时可切换“查询与解释”。</p>
          <button v-for="example in examples" :key="example" :disabled="busy || !status?.configured" @click="input = example">{{ example }} <span>↗</span></button>
        </div>
        <div v-else class="agent-messages" aria-live="polite">
          <article v-for="(message, index) in session.messages" :key="index" :class="['agent-message', message.role]">
            <strong>{{ message.role === 'user' ? '你' : '研究智能体' }}</strong><p>{{ message.content }}</p>
          </article>
        </div>
        <p v-if="busy" role="status" class="agent-working">正在处理，请稍候……</p>
        <form class="agent-composer" @submit.prevent="send()">
          <div class="agent-mode"><label for="agent-mode">处理方式</label><select id="agent-mode" v-model="mode" :disabled="busy || loading"><option value="plan">先规划</option><option value="chat">查询与解释</option></select></div>
          <label for="agent-input">研究需求</label>
          <textarea id="agent-input" v-model="input" :disabled="busy" maxlength="4000" rows="3" placeholder="例如：检查日线缺口，给出可修复范围；先展示方案再执行。" @keydown.ctrl.enter.prevent="send()" />
          <div><span>{{ mode === 'plan' ? '补充参数会生成新版本，并替代旧计划' : '查询结果以原服务与工具记录为准' }}</span><button :disabled="busy || loading || !status?.configured || !input.trim()"><Send :size="16" />{{ mode === 'plan' ? '生成计划' : '发送查询' }}</button></div>
        </form>
      </section>

      <aside class="agent-evidence" aria-label="操作与证据">
        <p v-if="plansNeedReload && !busy" role="alert" class="agent-error">计划状态尚未核实，批准已停用。请点击重新连接，再审阅当前版本。</p>
        <AgentPlanPanel :plans="session?.plans ?? []" :executions="session?.executions ?? []" :execution-busy="executionBusy" :busy="busy || loading || plansNeedReload" :tool-titles="Object.fromEntries((status?.tools ?? []).map(tool => [tool.name, tool.title]))" @decision="decidePlan" @control="controlPlan" @inspect="recoverPlanStep" />
        <section class="agent-panel">
          <h2>长期记忆 <span>{{ memories.length }}/32</span></h2>
          <p class="agent-muted">仅保存你明确提交的偏好、约束和经验，存储于本地 SQLite。请勿填写密钥；记忆不作为行情事实或执行授权。</p>
          <form class="agent-memory-form" @submit.prevent="saveMemory">
            <select v-model="memoryKind" aria-label="记忆分类"><option value="preference">偏好</option><option value="constraint">约束</option><option value="lesson">经验</option></select>
            <textarea v-model="memoryText" aria-label="长期记忆内容" maxlength="400" rows="2" placeholder="例如：研究结论优先说明数据日期和证据缺口" />
            <button :disabled="busy || loading || !memoryText.trim()">{{ editingMemory ? '明确保存修改' : '明确保存' }}</button>
            <button v-if="editingMemory" type="button" class="agent-secondary" @click="clearMemoryForm">取消编辑</button>
          </form>
          <article v-for="memory in memories" :key="memory.id" class="agent-memory">
            <p>{{ memory.content }}</p>
            <button class="agent-secondary" :disabled="busy || loading" @click="editMemory(memory)">编辑</button>
            <button class="agent-secondary" :disabled="busy || loading" @click="forget(memory)">删除此记忆</button>
          </article>
        </section>
        <section class="agent-panel"><h2>操作提案 <span>{{ session?.actions.length ?? 0 }}</span></h2><p v-if="!session?.actions.length" class="agent-muted">需要写入数据或运行研究时，具体参数和预检结果会出现在这里。</p>
          <article v-for="action in session?.actions ?? []" :key="action.action_id" class="agent-action">
            <h3>{{ action.title }}<span :class="['agent-tag', action.status]">{{ labels[action.status] }}</span></h3>
            <p>{{ action.preview.summary }}</p>
            <details :open="action.status === 'pending'"><summary>参数与影响</summary><pre>{{ pretty(action.parameters) }}</pre><pre>{{ pretty(action.preview) }}</pre></details>
            <p v-if="action.status === 'pending'" class="agent-muted">{{ !action.plan_id ? '独立预览没有计划授权，请在批准计划内重新预检。' : status?.write_enabled ? '逐项授权有效期最多10分钟；确认会重检来源与影响，再创建后台任务。' : '默认写执行关闭；可以审阅预检，确认此步骤尚未开放。' }}</p>
            <div v-if="action.status === 'pending'" class="agent-action-buttons"><button :disabled="busy || !status?.write_enabled || !action.plan_id" @click="decide(action.action_id, 'confirm')">确认此步骤</button><button class="agent-secondary" :disabled="busy" @click="decide(action.action_id, 'cancel')">取消</button></div>
            <div v-if="action.task_id" class="agent-task"><strong>任务 #{{ action.task_id }} · {{ labels[tasks[action.task_id]?.status ?? 'queued'] }}</strong><p>{{ tasks[action.task_id]?.error_message }}</p><details v-if="tasks[action.task_id]?.result"><summary>任务结果</summary><pre>{{ pretty(tasks[action.task_id]?.result) }}</pre></details><button class="agent-secondary" :disabled="busy" @click="send(`查询任务 ${action.task_id} 的状态与结果；若已完成，请复查并给出下一步建议。`, 'chat')">复查并继续</button></div>
          </article>
          <p v-if="taskError" role="alert" class="agent-error">{{ taskError }}</p>
        </section>
        <section class="agent-panel"><h2>工具记录 <span>{{ session?.steps.length ?? 0 }}</span></h2><p v-if="!session?.steps.length" class="agent-muted">这里显示实际查询与预检结果。无数据和失败会如实保留。</p><details v-for="(step, index) in session?.steps ?? []" :key="index" class="agent-step"><summary>{{ step.title }}</summary><pre>{{ pretty(step.result) }}</pre></details></section>
        <nav class="agent-links"><RouterLink to="/research">研究详情 ↗</RouterLink><RouterLink to="/operations">运维任务 ↗</RouterLink><RouterLink to="/portfolio">组合草案 ↗</RouterLink></nav>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.agent-composer .agent-mode { justify-content: flex-start; margin-bottom: 12px; }.agent-mode label { margin: 0; }.agent-mode select { font: inherit; font-size: 12px; padding: 7px; border: 1px solid #dfe5ef; border-radius: 6px; color: #52617a; background: white; }
.agent-history { display: flex; gap: 12px; align-items: center; margin-bottom: 18px; font-size: 13px; color: #63718a; }.agent-history select { max-width: 70%; padding: 8px; border: 1px solid #dfe5ef; border-radius: 6px; }.agent-memory-form { display: grid; gap: 8px; }.agent-memory-form textarea, .agent-memory-form select { font: inherit; font-size: 12px; border: 1px solid #dfe5ef; border-radius: 6px; padding: 8px; width: 100%; }.agent-memory p { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.7; }.agent-memory { border-top: 1px solid #edf0f5; margin-top: 12px; padding-top: 4px; }
.agent-page { max-width: 1600px; margin: 0 auto; padding: 30px; color: #1d293d; }
.agent-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
.agent-heading h1 { margin: 8px 0; font-size: 28px; letter-spacing: -.8px; }
.agent-heading p { margin: 0; color: #68758a; font-size: 14px; }
.agent-eyebrow { display: flex; align-items: center; gap: 6px; color: #4463d5 !important; font-size: 11px !important; font-weight: 700; letter-spacing: 1.4px; }
.agent-connection { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 14px 16px; border: 1px solid #dfe5ef; border-radius: 10px; margin-bottom: 22px; font-size: 12px; background: white; color: #68758a; }
.agent-connection strong { color: #39465a; }.agent-dot { width: 7px; height: 7px; border-radius: 50%; background: #b0b8c4; }.agent-dot.ready { background: #31977a; }
.agent-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, 1fr); align-items: start; gap: 22px; }
.agent-conversation, .agent-panel { background: white; border: 1px solid #dfe5ef; border-radius: 12px; }
.agent-conversation { overflow: hidden; min-height: 560px; display: flex; flex-direction: column; }.agent-welcome { padding: 44px 28px; flex: 1; }.agent-bot { display: inline-flex; padding: 13px; border-radius: 15px; background: #eef2ff; color: #4662d0; }.agent-welcome h2 { font-size: 21px; }.agent-welcome p { line-height: 1.8; font-size: 14px; color: #68758a; }.agent-welcome button { width: 100%; background: #fafbfe; color: #42516a; border: 1px solid #e2e7f1; border-radius: 8px; margin: 6px 0; text-align: left; font-size: 13px; line-height: 1.7; padding: 12px; display: flex; justify-content: space-between; gap: 12px; }
.agent-messages { padding: 24px; flex: 1; max-height: 65vh; overflow: auto; }.agent-message { padding: 15px; border-radius: 10px; margin-bottom: 16px; font-size: 14px; background: #f6f8fc; }.agent-message.user { margin-left: 30px; background: #edf2ff; }.agent-message strong { font-size: 11px; color: #62728e; }.agent-message p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; margin: 8px 0 0; }
.agent-composer { margin: 16px; padding: 14px; border: 1px solid #d8dfed; border-radius: 10px; }.agent-composer label { display: block; font-size: 12px; margin-bottom: 8px; color: #63718a; }.agent-composer textarea { width: 100%; resize: vertical; min-height: 80px; border: 0; outline: none; color: #26344c; background: transparent; font: inherit; font-size: 14px; line-height: 1.7; }.agent-composer > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.agent-composer span { font-size: 10px; color: #8995a8; }
button { cursor: pointer; border: 0; border-radius: 7px; padding: 9px 14px; color: white; background: #4663d4; display: inline-flex; align-items: center; justify-content: center; gap: 7px; font-size: 12px; }button:disabled { opacity: .5; cursor: default; }button:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid #4663d4; outline-offset: 3px; }.agent-secondary { background: white; color: #52617a; border: 1px solid #dce2ec; }.agent-icon { margin-left: auto; color: #68758a; background: transparent; padding: 3px; }
.agent-evidence { display: grid; gap: 18px; min-width: 0; }.agent-panel { padding: 20px; }.agent-panel h2 { font-size: 15px; margin: 0 0 16px; display: flex; justify-content: space-between; }.agent-panel h2 > span { color: #8995a8; }.agent-muted { color: #7a879a; font-size: 12px; line-height: 1.8; }.agent-action { border-top: 1px solid #edf0f5; padding: 16px 0; }.agent-action h3 { margin: 0; display: flex; gap: 8px; justify-content: space-between; font-size: 14px; }.agent-action p { font-size: 12px; line-height: 1.8; }.agent-tag { font-size: 10px; font-weight: 500; color: #738198; }.agent-tag.pending { color: #a26c18; }.agent-tag.confirmed { color: #31866e; }summary { cursor: pointer; font-size: 12px; color: #576987; padding: 8px 0; }pre { font-size: 11px; line-height: 1.65; background: #f7f9fc; border-radius: 6px; padding: 12px; max-height: 300px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }.agent-action-buttons { display: flex; gap: 8px; }.agent-task { background: #f5f8ff; padding: 12px; margin-top: 12px; border-radius: 8px; font-size: 12px; }.agent-step { border-top: 1px solid #edf0f5; }.agent-links { display: flex; flex-wrap: wrap; gap: 18px; font-size: 12px; }.agent-links a { color: #637697; text-decoration: none; }.agent-error { color: #a13d3d; font-size: 13px; padding: 12px; background: #fff3f3; border-radius: 8px; }.agent-working { padding: 0 26px; font-size: 12px; color: #6276a7; }
@media (max-width: 1100px) { .agent-grid { grid-template-columns: 1fr; }.agent-page { padding: 20px; } }
@media (max-width: 600px) { .agent-page { padding: 14px; }.agent-heading { align-items: flex-start; }.agent-heading h1 { font-size: 23px; }.agent-welcome { padding: 24px 18px; }.agent-composer span { display: none; }.agent-composer > div { justify-content: flex-end; }.agent-connection > span:not(.agent-dot) { line-height: 1.7; } }
</style>
