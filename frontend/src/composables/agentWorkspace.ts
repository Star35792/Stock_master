import { computed, ref } from "vue";

import {
  createAgentPlan, decideAgentPlan, controlAgentPlan, recoverAgentPlanStep,
  decideAgentAction,
  fetchAgentSession,
  fetchAgentStatus,
  fetchAgentTask,
  sendAgentMessage,
  fetchAgentSessions, fetchAgentMemories, saveAgentMemory, deleteAgentMemory,
  type AgentMemory, type AgentSessionSummary,
  type AgentSession,
  type AgentTask,
} from "../agentApi";

import { streamAgentRequest, type AgentStreamEvent } from "../agentStream";

const defaultDeps = {
  streamAgentRequest,
  createAgentPlan, decideAgentPlan, controlAgentPlan, recoverAgentPlanStep,
  fetchAgentStatus, fetchAgentSession, fetchAgentTask, sendAgentMessage, decideAgentAction,
  fetchAgentSessions, fetchAgentMemories, saveAgentMemory, deleteAgentMemory,
};

export function createAgentWorkspace(overrides: Partial<typeof defaultDeps> = {}) {
  const deps = { ...defaultDeps, ...overrides };
  const session = ref<AgentSession | null>(null);
  const status = ref<Awaited<ReturnType<typeof fetchAgentStatus>> | null>(null);
  const tasks = ref<Record<number, AgentTask>>({});
  const input = ref("");
  const mode = ref<"plan" | "chat">("chat");
  const streamEvents = ref<AgentStreamEvent[]>([]);
  const draft = ref("");
  const pendingMessage = ref("");
  const streamIncomplete = ref(false);
  const connected = ref(false);
  let streamSessionId: string | null = null;
  let streamController: AbortController | null = null;
  const plansNeedReload = ref(false);
  const busy = ref(false);
  const executionBusy = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const taskError = ref<string | null>(null);
  const sessions = ref<AgentSessionSummary[]>([]);
  const historyLoading = ref(false);
  const historyError = ref<string | null>(null);
  const memories = ref<AgentMemory[]>([]);
  const pendingActions = computed(() => session.value?.actions.filter(action => action.status === "pending") ?? []);
  const storageKey = "stock-master-agent-session";
  let disposed = false;
  let generation = 0;

  function saveSession(value: AgentSession, epoch = generation) {
    if (disposed || epoch !== generation) return;
    session.value = value;
    plansNeedReload.value = false;
    try { sessionStorage.setItem(storageKey, value.session_id); } catch { /* memory-only mode */ }
  }

  async function refreshTasks() {
    if (disposed) return;
    const epoch = generation;
    const ids = [...new Set(session.value?.actions.flatMap(action => action.task_id === null ? [] : [action.task_id]) ?? [])];
    taskError.value = null;
    await Promise.all(ids.map(async id => {
      if (["success", "partial", "failed"].includes(tasks.value[id]?.status ?? "")) return;
      try {
        const value = await deps.fetchAgentTask(id);
        if (!disposed && epoch === generation) tasks.value = { ...tasks.value, [id]: value };
      } catch (cause) {
        if (!disposed && epoch === generation) taskError.value = cause instanceof Error ? cause.message : "任务进度读取失败";
      }
    }));
  }

  async function refreshHistory() {
    if (disposed || historyLoading.value) return;
    const epoch = generation;
    historyLoading.value = true;
    historyError.value = null;
    try {
      const items = await deps.fetchAgentSessions();
      if (!disposed && epoch === generation) sessions.value = items;
    } catch (cause) {
      if (!disposed && epoch === generation) historyError.value = cause instanceof Error ? cause.message : "历史对话读取失败";
    } finally { historyLoading.value = false; }
  }

  async function load() {
    if (busy.value || disposed) return;
    const epoch = ++generation;
    loading.value = true;
    connected.value = false;
    error.value = null;
    try {
      const value = await deps.fetchAgentStatus();
      if (disposed || epoch !== generation) return;
      status.value = value;
      connected.value = true;
      const [savedSessions, savedMemories] = await Promise.all([deps.fetchAgentSessions(), deps.fetchAgentMemories()]);
      if (disposed || epoch !== generation) return;
      sessions.value = savedSessions;
      memories.value = savedMemories;
      let saved: string | null = null;
      try { saved = sessionStorage.getItem(storageKey); } catch { /* memory-only mode */ }
      if (saved && !disposed) {
        saveSession(await deps.fetchAgentSession(saved), epoch);
        pendingMessage.value = "";
        draft.value = "";
        if (disposed || epoch !== generation) return;
        await refreshTasks();
      }
    } catch (cause) {
      if (!disposed && epoch === generation) error.value = cause instanceof Error ? cause.message : "智能体连接失败";
    } finally { if (!disposed && epoch === generation) loading.value = false; }
  }

  async function send(message = input.value, requestMode = mode.value) {
    const text = message.trim();
    if (busy.value || loading.value || !text || !status.value?.configured || disposed) return;
    busy.value = true;
    const epoch = ++generation;
    error.value = null;
    pendingMessage.value = text;
    draft.value = "";
    streamEvents.value = [];
    streamIncomplete.value = false;
    streamController = new AbortController();
    if (requestMode === "plan") plansNeedReload.value = true;
    try {
      const value = await deps.streamAgentRequest(requestMode, text, session.value?.session_id ?? streamSessionId, event => {
        if (disposed || epoch !== generation) return;
        if (event.type === "session" && event.session_id) {
          streamSessionId = event.session_id;
          input.value = "";
          try { sessionStorage.setItem(storageKey, event.session_id); } catch { /* memory-only mode */ }
        }
        if (event.type === "round" || event.type === "draft_reset") draft.value = "";
        if (event.type === "text" && typeof event.text === "string") draft.value += event.text;
        else if (!["session", "done"].includes(event.type)) streamEvents.value = [...streamEvents.value, event].slice(-40);
      }, streamController.signal);
      if (disposed || epoch !== generation) return;
      saveSession(value, epoch);
      pendingMessage.value = "";
      draft.value = "";
      input.value = "";
      await refreshTasks();
    } catch (cause) {
      if (disposed || epoch !== generation) return;
      streamIncomplete.value = true;
      error.value = streamController?.signal.aborted ? "已停止接收；请载入原会话核对已开始的查询。" : cause instanceof Error ? cause.message : "智能体请求失败";
      const knownId = streamSessionId ?? session.value?.session_id;
      if (requestMode === "plan" && knownId) {
        try {
          const restored = await deps.fetchAgentSession(knownId);
          if (disposed || epoch !== generation) return;
          saveSession(restored, epoch);
          pendingMessage.value = "";
          draft.value = "";
        }
        catch { /* Keep approvals disabled until an explicit reload succeeds. */ }
      }
    } finally { if (!disposed && epoch === generation) { busy.value = false; streamController = null; } }
  }

  function stopResponse() {
    streamIncomplete.value = true;
    streamController?.abort();
  }

  async function decide(actionId: string, decision: "confirm" | "cancel") {
    if (busy.value || !session.value || disposed) return;
    busy.value = true;
    error.value = null;
    try {
      saveSession(await deps.decideAgentAction(actionId, session.value.session_id, decision));
      await refreshTasks();
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "操作确认失败";
    } finally { busy.value = false; }
  }

  async function decidePlan(planId: string, decision: "approve" | "reject") {
    if (busy.value || loading.value || plansNeedReload.value || !session.value || disposed) return;
    const plan = session.value.plans?.find(item => item.plan_id === planId);
    if (!plan) return;
    busy.value = true;
    error.value = null;
    try {
      saveSession(await deps.decideAgentPlan(plan, session.value.session_id, decision));
    } catch (cause) {
      plansNeedReload.value = true;
      error.value = cause instanceof Error ? cause.message : "计划批准失败，请重新载入后审阅";
    } finally { busy.value = false; }
  }

  async function controlPlan(planId: string, operation: "advance" | "refresh" | "cancel") {
    if (disposed || loading.value || !session.value || plansNeedReload.value) return;
    if (busy.value && !(operation === "cancel" && executionBusy.value)) return;
    const plan = session.value.plans?.find(item => item.plan_id === planId);
    if (!plan) return;
    const epoch = ++generation;
    busy.value = true;
    executionBusy.value = operation === "advance";
    error.value = null;
    try { saveSession(await deps.controlAgentPlan(plan, session.value.session_id, operation), epoch); }
    catch (cause) {
      if (epoch === generation && !disposed) {
        plansNeedReload.value = true;
        error.value = cause instanceof Error ? cause.message : "计划执行状态待核实，请重新载入";
      }
    } finally {
      if (epoch === generation && !disposed) { busy.value = false; executionBusy.value = false; }
    }
  }

  async function recoverPlanStep(planId: string, stepId: string) {
    if (disposed || busy.value || loading.value || !session.value || plansNeedReload.value) return;
    const plan = session.value.plans?.find(item => item.plan_id === planId);
    if (!plan) return;
    const epoch = ++generation;
    busy.value = true;
    executionBusy.value = true;
    error.value = null;
    try { saveSession(await deps.recoverAgentPlanStep(plan, session.value.session_id, stepId), epoch); }
    catch (cause) {
      if (epoch === generation && !disposed) {
        plansNeedReload.value = true;
        error.value = cause instanceof Error ? cause.message : "原任务证据暂不可用，请重新载入后核验";
      }
    } finally {
      if (epoch === generation && !disposed) { busy.value = false; executionBusy.value = false; }
    }
  }

  function reset() {
    if (busy.value) return;
    generation += 1;
    loading.value = false;
    session.value = null;
    plansNeedReload.value = false;
    error.value = null;
    input.value = "";
    tasks.value = {};
    streamSessionId = null;
    pendingMessage.value = "";
    draft.value = "";
    streamEvents.value = [];
    streamIncomplete.value = false;
    try { sessionStorage.removeItem(storageKey); } catch { /* memory-only mode */ }
  }

  async function restore(id: string) {
    if (busy.value) return;
    const epoch = ++generation;
    loading.value = true;
    error.value = null;
    try {
      saveSession(await deps.fetchAgentSession(id), epoch);
      pendingMessage.value = "";
      draft.value = "";
      streamEvents.value = [];
      streamIncomplete.value = false;
      streamSessionId = id;
      if (epoch === generation && !disposed) { tasks.value = {}; await refreshTasks(); }
    } catch (cause) {
      if (epoch === generation && !disposed) error.value = cause instanceof Error ? cause.message : "会话恢复失败";
    } finally { if (epoch === generation && !disposed) loading.value = false; }
  }

  async function remember(kind: AgentMemory["kind"], content: string, existing?: AgentMemory) {
    if (busy.value || loading.value || !content.trim()) return false;
    busy.value = true;
    error.value = null;
    try {
      memories.value = await deps.saveAgentMemory({ kind, content, source_session_id: session.value?.session_id ?? null, ...(existing ? { id: existing.id, revision: existing.revision } : {}) });
      return true;
    } catch (cause) { error.value = cause instanceof Error ? cause.message : "记忆保存失败"; return false; }
    finally { busy.value = false; }
  }

  async function forget(memory: AgentMemory) {
    if (busy.value || loading.value) return;
    busy.value = true;
    error.value = null;
    try { memories.value = await deps.deleteAgentMemory(memory.id, memory.revision); }
    catch (cause) { error.value = cause instanceof Error ? cause.message : "记忆删除失败"; }
    finally { busy.value = false; }
  }

  return { session, status, tasks, input, mode, streamEvents, draft, pendingMessage, streamIncomplete, connected, stopResponse, plansNeedReload, busy, executionBusy, loading, error, taskError, pendingActions, sessions, historyLoading, historyError, refreshHistory, memories, restore, remember, forget, load, send, decide, decidePlan, controlPlan, recoverPlanStep, reset, refreshTasks, dispose: () => { disposed = true; generation += 1; streamController?.abort(); } };
}
