<script setup lang="ts">
import { ArrowUp, Check, ChevronDown, History, LoaderCircle, MessageSquarePlus, RefreshCw, Settings2, Sparkles, Square, X } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { AgentMemory } from "../agentApi";
import AgentPlanPanel from "../components/AgentPlanPanel.vue";
import AgentSkillPanel from "../components/AgentSkillPanel.vue";
import { createAgentWorkspace } from "../composables/agentWorkspace";

const {
  session, status, tasks, input, mode, streamEvents, draft, pendingMessage, streamIncomplete,
  connected, stopResponse, plansNeedReload, busy, executionBusy, loading, error, taskError,
  sessions, historyLoading, historyError, refreshHistory, memories, restore, remember, forget,
  load, send, decide, decidePlan, controlPlan, recoverPlanStep, reset, refreshTasks, dispose,
} = createAgentWorkspace();
const messagePane = ref<HTMLElement | null>(null);
const inputElement = ref<HTMLTextAreaElement | null>(null);
const dialog = ref<HTMLDialogElement | null>(null);
const panel = ref<"settings" | "history">("settings");
const memoryText = ref("");
const memoryKind = ref<AgentMemory["kind"]>("preference");
const editingMemory = ref<AgentMemory | null>(null);
const followMessages = ref(true);
const hasConversation = computed(() => Boolean(session.value?.messages.length || pendingMessage.value || session.value?.plans?.length || session.value?.actions.length));
const skillChecks = computed(() => [...(session.value?.skill_checks ?? []), ...(session.value?.executions ?? []).flatMap(item => item.skill_checks ?? [])]);
const connectionLabel = computed(() => loading.value ? "正在连接" : !connected.value ? "连接不可用" : status.value?.configured ? status.value.model : "模型未配置");
const processLabel = computed(() => streamIncomplete.value ? "本次响应未完成 · 查看过程" : busy.value ? [...streamEvents.value].reverse().find(event => event.message)?.message ?? "正在思考…" : "查看本次处理过程");
const labels: Record<string, string> = { starting: "创建结果待核实", pending: "等待确认", confirmed: "已确认", cancelled: "已取消", expired: "已过期", queued: "已排队", running: "执行中", success: "成功", partial: "部分完成", failed: "失败" };
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

function trackScroll() {
  const pane = messagePane.value;
  if (pane) followMessages.value = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
}
function resizeInput() {
  const element = inputElement.value;
  if (element) { element.style.height = "auto"; element.style.height = `${Math.min(element.scrollHeight, 180)}px`; }
}
function handleInputKey(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    void send();
  }
}
async function focusInput() { await nextTick(); inputElement.value?.focus(); }
function newConversation() { reset(); mode.value = "chat"; followMessages.value = true; void focusInput(); }
function openPanel(value: "settings" | "history") {
  panel.value = value;
  dialog.value?.showModal();
  if (value === "history") void refreshHistory();
}
function closeOnBackdrop(event: MouseEvent) {
  const element = dialog.value;
  if (!element || event.target !== element) return;
  const bounds = element.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) element.close();
}
async function restoreConversation(id: string) {
  followMessages.value = true;
  await restore(id);
  if (!error.value) { dialog.value?.close(); void focusInput(); }
}
function selectSkill(name: string) { input.value = `/${name} `; dialog.value?.close(); void focusInput(); }
function editMemory(memory: AgentMemory) { editingMemory.value = memory; memoryKind.value = memory.kind; memoryText.value = memory.content; }
function clearMemoryForm() { editingMemory.value = null; memoryText.value = ""; }
async function saveMemory() { if (await remember(memoryKind.value, memoryText.value, editingMemory.value ?? undefined)) clearMemoryForm(); }

watch(input, async () => { await nextTick(); resizeInput(); });
watch(pendingMessage, value => { if (value) followMessages.value = true; });
watch([draft, pendingMessage, () => session.value], async () => {
  await nextTick();
  if (followMessages.value) messagePane.value?.scrollTo({ top: messagePane.value.scrollHeight });
});
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(async () => {
  await load();
  resizeInput();
  timer = setInterval(() => { if (!busy.value) void refreshTasks(); }, 4000);
});
onBeforeUnmount(() => { clearInterval(timer); dispose(); });
</script>

<template>
  <div class="agent-page" :class="{ 'is-empty': !hasConversation }">
    <header class="agent-heading">
      <div class="agent-title"><Sparkles :size="19" aria-hidden="true" /><h1>智能体</h1></div>
      <nav class="agent-toolbar" aria-label="对话操作">
        <button :disabled="busy || loading" title="新建对话" @click="newConversation"><MessageSquarePlus :size="18" aria-hidden="true" /><span>新对话</span></button>
        <button :disabled="busy || loading" title="历史对话" aria-label="历史对话" @click="openPanel('history')"><History :size="18" aria-hidden="true" /></button>
        <button title="设置" aria-label="智能体设置" @click="openPanel('settings')"><Settings2 :size="18" aria-hidden="true" /></button>
      </nav>
    </header>

    <section class="agent-conversation" aria-label="研究对话">
      <div ref="messagePane" class="agent-scroll" @scroll.passive="trackScroll">
        <div class="agent-content">
          <div v-if="!hasConversation" class="agent-welcome">
            <div class="agent-mark"><Sparkles :size="30" :stroke-width="1.5" aria-hidden="true" /></div>
            <h2>今天想聊些什么？</h2>
            <p>聊聊行情、研究或持仓，直接问我就好。</p>
          </div>
          <div v-else class="agent-messages" aria-label="对话记录" aria-live="polite" :aria-busy="busy && !!pendingMessage">
            <article v-for="(message, index) in session?.messages ?? []" :key="index" :class="['agent-message', message.role]">
              <span class="agent-speaker">{{ message.role === 'user' ? '你' : '智能体' }}</span><p>{{ message.content }}</p>
            </article>
            <article v-if="pendingMessage" class="agent-message user"><span class="agent-speaker">你</span><p>{{ pendingMessage }}</p></article>
            <article v-if="draft" class="agent-message assistant"><span class="agent-speaker">{{ streamIncomplete ? '未完成答复' : '智能体' }}</span><p>{{ draft }}</p><span v-if="busy" class="agent-cursor" aria-hidden="true" /></article>
            <p v-if="busy && pendingMessage && !draft && !streamEvents.length" class="agent-thinking" role="status"><LoaderCircle :size="16" class="agent-spin" aria-hidden="true" />正在思考…</p>
          </div>

          <details v-if="streamEvents.length" class="agent-process">
            <summary><LoaderCircle v-if="busy" :size="14" class="agent-spin" aria-hidden="true" /><span role="status">{{ processLabel }}</span><ChevronDown :size="14" aria-hidden="true" /></summary>
            <ol><li v-for="(event, index) in streamEvents" :key="index"><span>{{ event.message ?? event.title ?? event.tool }}</span><details v-if="event.parameters || event.result"><summary>参数与结果</summary><pre>{{ pretty(event.parameters ?? event.result) }}</pre></details></li></ol>
          </details>

          <div v-if="session?.plans?.length || session?.actions.length || plansNeedReload" class="agent-followup">
            <p v-if="plansNeedReload && !busy" role="alert" class="agent-error">计划状态尚未核实，批准已停用。请载入原会话，再审阅当前版本。<button :disabled="loading" @click="load">载入原会话</button></p>
            <AgentPlanPanel v-if="session?.plans?.length" :plans="session.plans" :executions="session.executions ?? []" :execution-busy="executionBusy" :busy="busy || loading || plansNeedReload" :tool-titles="Object.fromEntries((status?.tools ?? []).map(tool => [tool.name, tool.title]))" @decision="decidePlan" @control="controlPlan" @inspect="recoverPlanStep" @explain="send(`请解释第${$event}版计划的已保存执行结果，引用真实ID、日期和原服务指标，分别说明事实、缺失与推断及限制；不要生成新写预览。`, 'chat')" />
            <section v-if="session?.actions.length" class="agent-panel" aria-label="操作提案">
              <h2>操作提案</h2>
              <article v-for="action in session.actions" :key="action.action_id" class="agent-action">
                <h3>{{ action.title }}<span class="agent-tag">{{ labels[action.status] ?? action.status }}</span></h3>
                <p>{{ action.preview.summary }}</p>
                <details :open="action.status === 'pending'"><summary>参数与影响</summary><pre>{{ pretty(action.parameters) }}</pre><pre>{{ pretty(action.preview) }}</pre></details>
                <p v-if="action.status === 'pending'" class="agent-muted">{{ !action.plan_id ? '独立预览没有计划授权，请在批准计划内重新预检。' : status?.write_enabled ? '确认会重检来源与影响，再创建后台任务。授权最多有效10分钟。' : '写执行默认关闭；目前仅可审阅此步骤。' }}</p>
                <div v-if="action.status === 'pending'" class="agent-action-buttons"><button :disabled="busy || loading || plansNeedReload || !status?.write_enabled || !action.plan_id" @click="decide(action.action_id, 'confirm')">确认此步骤</button><button class="agent-secondary" :disabled="busy || loading" @click="decide(action.action_id, 'cancel')">取消</button></div>
                <div v-if="action.task_id" class="agent-task"><strong>任务 #{{ action.task_id }} · {{ labels[tasks[action.task_id]?.status ?? 'queued'] }}</strong><p>{{ tasks[action.task_id]?.error_message }}</p><details v-if="tasks[action.task_id]?.result"><summary>任务结果</summary><pre>{{ pretty(tasks[action.task_id]?.result) }}</pre></details><button class="agent-secondary" :disabled="busy || loading" @click="send(`查询任务 ${action.task_id} 的状态与结果；若已完成，请复查并给出下一步建议。`, 'chat')">复查并继续</button></div>
              </article>
            </section>
          </div>
          <p v-if="taskError" role="alert" class="agent-error">{{ taskError }}</p>
          <details v-if="session?.steps.length || skillChecks.length" class="agent-records">
            <summary>查看工具记录与检查材料</summary>
            <details v-for="(step, index) in session?.steps ?? []" :key="index"><summary>{{ step.title }}</summary><pre>{{ pretty(step.result) }}</pre></details>
            <AgentSkillPanel v-if="skillChecks.length" :skills="[]" :checks="skillChecks" :busy="busy || loading" @select="selectSkill" />
          </details>
        </div>
      </div>

      <div class="agent-input-area">
        <p v-if="error" role="alert" class="agent-error">{{ error }}</p>
        <p v-if="!loading && (!connected || !status?.configured)" role="status" class="agent-notice">{{ !connected ? '暂时无法连接智能体。' : '请先在后端配置 DeepSeek。' }}<button :disabled="busy" @click="openPanel('settings')">查看设置</button></p>
        <div v-if="streamIncomplete" class="agent-notice" role="status"><span>答复未完成，草稿仅供参考。停止接收不会撤销已开始的查询。</span><button :disabled="busy || loading" @click="load">载入原会话</button></div>
        <form class="agent-composer" @submit.prevent="send()">
          <label for="agent-input" class="agent-sr-only">发送消息</label>
          <textarea id="agent-input" ref="inputElement" v-model="input" :disabled="busy || loading" maxlength="4000" rows="2" placeholder="发消息，聊聊你的想法…" @keydown="handleInputKey" />
          <div class="agent-composer-footer">
            <label class="agent-mode"><span class="agent-sr-only">处理方式</span><select v-model="mode" :disabled="busy || loading" aria-label="处理方式"><option value="chat">对话</option><option value="plan">先规划</option></select></label>
            <span class="agent-key-hint">{{ mode === 'plan' ? '先审阅计划，再确认执行' : 'Enter 发送 · Shift + Enter 换行' }}</span>
            <button v-if="busy && pendingMessage" type="button" class="agent-send agent-stop" aria-label="停止接收" title="停止接收" @click="stopResponse"><Square :size="15" fill="currentColor" aria-hidden="true" /></button>
            <button v-else type="submit" class="agent-send" :disabled="busy || loading || !status?.configured || !connected || !input.trim()" :aria-label="mode === 'plan' ? '生成计划' : '发送消息'" :title="mode === 'plan' ? '生成计划' : '发送消息'"><ArrowUp :size="20" aria-hidden="true" /></button>
          </div>
        </form>
        <p class="agent-footnote">AI 回答仅供研究参考，请核对重要信息。</p>
      </div>
    </section>

    <dialog ref="dialog" class="agent-dialog" :aria-labelledby="panel === 'settings' ? 'agent-settings-title' : 'agent-history-title'" @click="closeOnBackdrop">
      <header class="agent-dialog-heading"><h2 :id="panel === 'settings' ? 'agent-settings-title' : 'agent-history-title'">{{ panel === 'settings' ? '设置' : '历史对话' }}</h2><button type="button" class="agent-close" aria-label="关闭" autofocus @click="dialog?.close()"><X :size="19" aria-hidden="true" /></button></header>
      <p v-if="error" role="alert" class="agent-error">{{ error }}</p>
      <template v-if="panel === 'history'">
        <p v-if="historyLoading" class="agent-muted" role="status">正在读取历史对话…</p>
        <p v-if="historyError" role="alert" class="agent-error">{{ historyError }}<button :disabled="historyLoading" @click="refreshHistory">重试</button></p>
        <p v-else-if="!historyLoading && !sessions.length" class="agent-empty-history">还没有历史对话，从第一条消息开始吧。</p>
        <div class="agent-history-list"><button v-for="item in sessions" :key="item.session_id" :disabled="busy || loading" :aria-current="item.session_id === session?.session_id ? 'true' : undefined" @click="restoreConversation(item.session_id)"><span>{{ item.title }}</span><Check v-if="item.session_id === session?.session_id" :size="16" aria-label="当前对话" /></button></div>
      </template>
      <div v-else class="agent-settings">
        <details open class="agent-setting-section">
          <summary>偏好与记忆 <span>{{ memories.length }}/32</span></summary>
          <p class="agent-muted">语气、回答详略等偏好可保存在这里，只在你点击保存后记住。</p>
          <form class="agent-memory-form" @submit.prevent="saveMemory">
            <select v-model="memoryKind" aria-label="记忆分类" :disabled="busy || loading"><option value="preference">偏好</option><option value="constraint">约束</option><option value="lesson">经验</option></select>
            <textarea v-model="memoryText" aria-label="长期记忆内容" :disabled="busy || loading" maxlength="400" rows="3" placeholder="例如：用自然、简洁的语气回答，先给结论" />
            <div class="agent-action-buttons"><button :disabled="busy || loading || !memoryText.trim()">{{ editingMemory ? '保存修改' : '保存记忆' }}</button><button v-if="editingMemory" type="button" class="agent-secondary" @click="clearMemoryForm">取消编辑</button></div>
          </form>
          <article v-for="memory in memories" :key="memory.id" class="agent-memory"><p>{{ memory.content }}</p><div class="agent-action-buttons"><button class="agent-secondary" :disabled="busy || loading" @click="editMemory(memory)">编辑</button><button class="agent-secondary" :disabled="busy || loading" @click="forget(memory)">删除</button></div></article>
          <p class="agent-muted">记忆仅保存在本地，请勿填写密钥。偏好不作为行情事实或操作授权。</p>
        </details>
        <details class="agent-setting-section"><summary>专业技能</summary><AgentSkillPanel :skills="status?.skills ?? []" :checks="[]" :busy="busy || loading" @select="selectSkill" /></details>
        <details class="agent-setting-section"><summary>连接与使用说明 <span class="agent-dot" :class="{ ready: connected && status?.configured }" /></summary>
          <p class="agent-connection" role="status">{{ connectionLabel }}<button class="agent-secondary" :disabled="loading || busy" @click="load"><RefreshCw :size="14" aria-hidden="true" />重新连接</button></p>
          <p v-if="connected && !status?.configured" class="agent-muted">请在后端配置 DEEPSEEK_API_KEY 并重启服务。</p>
          <p class="agent-muted">需求与对话会发送给 DeepSeek。普通对话查询现有数据；采集、回测等操作请在输入框选择“先规划”。批准计划不会执行，写步骤仍需逐项确认。{{ status?.write_enabled ? '当前允许在授权范围内确认写步骤。' : '当前写执行默认关闭。' }}</p>
          <p class="agent-muted">技能检查材料齐备不代表研究目标或策略资格已认证。</p>
        </details>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.agent-page { height: 100%; min-height: 0; display: flex; flex-direction: column; color: #253047; background: #fcfcfd; }
.agent-heading { display: flex; align-items: center; justify-content: space-between; flex: 0 0 auto; padding: 18px 28px; }
.agent-title, .agent-toolbar, .agent-composer-footer, .agent-action-buttons { display: flex; align-items: center; gap: 10px; }
.agent-title { color: #5264b4; }.agent-title h1 { margin: 0; color: #354057; font-size: 15px; font-weight: 600; }
button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 9px 14px; border: 1px solid transparent; border-radius: 9px; color: white; background: #4663d4; font: inherit; font-size: 12px; cursor: pointer; }
button:disabled { opacity: .45; cursor: default; }button:focus-visible, textarea:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid #6b7ddd; outline-offset: 3px; }
.agent-toolbar { gap: 4px; }.agent-toolbar button, .agent-close { min-width: 36px; height: 36px; padding: 8px; color: #71798b; background: transparent; }.agent-toolbar button:hover, .agent-close:hover { background: #eceef4; color: #293650; }
.agent-toolbar button:first-child { padding-inline: 12px; margin-right: 4px; }
.agent-conversation { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.agent-scroll { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; }
.agent-content, .agent-input-area { width: min(100%, 800px); padding-inline: 28px; margin-inline: auto; }
.agent-content { padding-block: 24px; }.agent-welcome { text-align: center; padding-block: 24px; }.agent-mark { display: inline-flex; color: #546bc7; margin-bottom: 15px; }.agent-welcome h2 { margin: 0 0 14px; font-size: clamp(23px, 3vw, 30px); font-weight: 600; letter-spacing: -.7px; }.agent-welcome p { margin: 0; font-size: 14px; color: #8a90a0; line-height: 1.8; }
.is-empty .agent-conversation { justify-content: center; padding-bottom: min(14vh, 140px); }.is-empty .agent-scroll { flex: 0 1 auto; }.is-empty .agent-content { padding-block: 8px 20px; }
.agent-message { margin-bottom: 30px; font-size: 15px; line-height: 1.95; overflow-wrap: anywhere; }.agent-speaker { font-size: 11px; color: #8991a0; }.agent-message p { white-space: pre-wrap; margin: 6px 0 0; }.agent-message.user { max-width: 85%; width: fit-content; margin-left: auto; padding: 10px 18px; border-radius: 17px 17px 4px 17px; background: #eef0f6; }.agent-message.user .agent-speaker { display: none; }.agent-message.user p { margin: 0; }.agent-message.assistant { padding-block: 4px 8px; }
.agent-cursor { display: inline-block; height: 7px; width: 7px; border-radius: 50%; background: #7183c7; }.agent-thinking { display: flex; align-items: center; gap: 8px; color: #7b8497; font-size: 13px; }
.agent-input-area { flex-shrink: 0; padding-top: 12px; padding-bottom: max(8px, env(safe-area-inset-bottom)); }.agent-composer { padding: 16px 16px 12px; border: 1px solid #e0e3eb; border-radius: 20px; background: white; box-shadow: 0 4px 24px #24345a06; transition: border-color .15s, box-shadow .15s; }.agent-composer:focus-within { border-color: #a6b2db; box-shadow: 0 4px 24px #24345a0a; }.agent-composer textarea { display: block; width: 100%; min-height: 58px; max-height: 180px; resize: none; padding: 0; margin-bottom: 10px; border: 0; background: transparent; color: #29354c; font: inherit; font-size: 15px; line-height: 1.7; outline: none; }.agent-composer textarea::placeholder { color: #a2a7b3; }.agent-composer textarea:focus-visible { outline: none; }.agent-composer-footer { gap: 12px; }.agent-mode select { border: 0; border-radius: 7px; padding: 6px 8px; background: #f3f4f7; color: #606b80; font: inherit; font-size: 12px; cursor: pointer; }.agent-key-hint { color: #a2a8b5; font-size: 10px; }.agent-send { margin-left: auto; flex-shrink: 0; padding: 8px; height: 36px; width: 36px; border-radius: 50%; background: #485a93; }.agent-stop { background: #354263; }.agent-footnote { margin: 10px 0 0; color: #a2a8b3; font-size: 10px; text-align: center; }
.agent-muted { font-size: 12px; color: #7e8798; line-height: 1.85; }.agent-notice, .agent-error { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; color: #916137; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }.agent-error { color: #ad4e4e; }.agent-notice button, .agent-error button { padding: 2px 0; color: inherit; background: transparent; text-decoration: underline; }.agent-followup { display: grid; gap: 16px; margin-block: 20px; }
details { min-width: 0; }summary { padding: 10px 0; color: #7b8597; font-size: 12px; cursor: pointer; }pre { max-height: 260px; overflow: auto; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 8px; background: #f4f6fa; font-size: 11px; line-height: 1.8; }
.agent-process { margin-block: 10px 20px; }.agent-process > summary { display: flex; align-items: center; gap: 8px; list-style: none; }.agent-process > summary::-webkit-details-marker { display: none; }.agent-process > summary > span { flex: 1; }.agent-process[open] > summary > svg:last-child { transform: rotate(180deg); }.agent-process ol { padding-left: 20px; max-height: 260px; overflow: auto; color: #718098; font-size: 12px; line-height: 1.8; }.agent-process li { padding-block: 4px; }.agent-records { border-top: 1px solid #eceef3; margin-block: 20px; }
.agent-panel { min-width: 0; border: 1px solid #e2e6ef; border-radius: 12px; padding: 20px; background: white; }.agent-panel h2 { font-size: 15px; margin: 0 0 16px; }.agent-action + .agent-action { border-top: 1px solid #eceef3; margin-top: 16px; padding-top: 16px; }.agent-action h3 { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin: 0; font-size: 14px; }.agent-action p { font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }.agent-tag { color: #7989af; font-size: 11px; }.agent-action-buttons { flex-wrap: wrap; }.agent-secondary { background: white; color: #637089; border-color: #e1e5ee; }.agent-task { margin-top: 14px; padding: 12px; border-radius: 8px; background: #f5f7fc; font-size: 12px; }
.agent-dialog { width: min(520px, calc(100vw - 32px)); max-height: min(80dvh, 760px); margin: auto; padding: 24px; border: 1px solid #e2e5ed; border-radius: 18px; color: #2d3950; background: #fff; box-shadow: 0 24px 90px #19233824; overflow-y: auto; }.agent-dialog::backdrop { background: #1b254044; backdrop-filter: blur(3px); }.agent-dialog-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 18px; }.agent-dialog-heading h2 { font-size: 18px; margin: 0; }.agent-setting-section { border-top: 1px solid #eceef3; padding-block: 8px; }.agent-setting-section > summary { font-size: 13px; color: #43516a; }.agent-setting-section > summary > span { float: right; font-size: 11px; color: #8a94a5; }.agent-memory-form { display: grid; gap: 10px; }.agent-memory-form textarea, .agent-memory-form select { width: 100%; border: 1px solid #e0e4ed; border-radius: 8px; padding: 10px; color: #43516a; background: white; font: inherit; font-size: 12px; }.agent-memory-form textarea { resize: vertical; }.agent-memory { padding-block: 12px; border-bottom: 1px solid #eff1f6; }.agent-memory p { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.8; }.agent-connection { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; }.agent-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin: 5px; background: #c3a67c; }.agent-dot.ready { background: #57a88a; }.agent-history-list { display: grid; gap: 6px; }.agent-history-list button { justify-content: space-between; width: 100%; text-align: left; color: #56637a; background: #f6f7fa; padding: 14px; line-height: 1.7; }.agent-history-list button[aria-current] { color: #4e62ad; background: #eff2fc; }.agent-history-list button > span { flex: 1; overflow-wrap: anywhere; }.agent-empty-history { padding-block: 30px; text-align: center; color: #8b95a5; font-size: 13px; }
.agent-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }.agent-spin { animation: agent-spin 1.4s linear infinite; }@keyframes agent-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .agent-spin { animation: none; } }
@media (max-width: 760px) { .agent-heading { padding: 12px 16px; }.agent-content, .agent-input-area { padding-inline: 16px; }.agent-content { padding-top: 16px; }.agent-message { font-size: 14px; }.agent-message.user { max-width: 92%; }.agent-welcome p { font-size: 12px; }.agent-key-hint { display: none; }.is-empty .agent-conversation { padding-bottom: 6vh; }.agent-composer { padding: 14px 12px 10px; border-radius: 16px; }.agent-dialog { padding: 18px; }.agent-toolbar button:first-child { padding-inline: 8px; }.agent-composer textarea { min-height: 48px; } }
@media (max-height: 560px) { .is-empty .agent-conversation { padding-bottom: 0; }.agent-mark { display: none; }.agent-welcome { padding-block: 4px; }.agent-welcome p { display: none; } }
</style>
