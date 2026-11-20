import { computed, ref, watch } from "vue";

import {
  createAgentPlan, decideAgentPlan, controlAgentPlan, recoverAgentPlanStep,
  decideAgentAction,
  fetchAgentSession,
  fetchAgentStatus,
  fetchAgentTask,
  forkAgentSession,
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
  forkAgentSession,
};

function isFinishedTask(task: AgentTask | undefined) {
  return task?.status === "success" || task?.status === "partial" || task?.status === "failed";
}

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
  const contextWindowed = ref(false);
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
  let trackingBusy = false;
  const autoContinue = new Set<string>();

  const draftKey = (id: string) => `${storageKey}-draft-${id}`;
  let regularDraft = "";
  const stopWatchingDraft = watch(input, text => {
    const active = session.value;
    if (!active?.forked_from) {
      regularDraft = text;
      return;
    }
    try {
      const key = draftKey(active.session_id);
      if (text) sessionStorage.setItem(key, text);
      else sessionStorage.removeItem(key);
    } catch { /* memory-only mode */ }
  }, { flush: "sync" });

  function canWrite(tool: string) {
    return status.value?.tools?.find(item => item.name === tool)?.write_enabled
      ?? status.value?.write_enabled ?? false;
  }

  function saveSession(value: AgentSession, epoch = generation) {
    if (disposed || epoch !== generation) return;
    session.value = value;
    streamSessionId = value.session_id;
    plansNeedReload.value = false;
    try { sessionStorage.setItem(storageKey, value.session_id); } catch { /* memory-only mode */ }
  }

  function openSavedSession(value: AgentSession, epoch: number, newDraft?: string) {
    if (disposed || epoch !== generation) return false;
    autoContinue.clear();
    saveSession(value, epoch);
    let savedDraft = "";
    try { if (value.forked_from) savedDraft = sessionStorage.getItem(draftKey(value.session_id)) ?? ""; } catch { /* memory-only mode */ }
    input.value = newDraft ?? (value.forked_from ? savedDraft : regularDraft);
    pendingMessage.value = "";
    draft.value = "";
    streamEvents.value = [];
    streamIncomplete.value = false;
    contextWindowed.value = false;
    tasks.value = {};
    taskError.value = null;
    return true;
  }

  async function refreshTasks() {
    if (disposed) return;
    const epoch = generation;
    const ids = [...new Set(session.value?.actions.flatMap(action => action.task_id === null ? [] : [action.task_id]) ?? [])];
    taskError.value = null;
    await Promise.all(ids.map(async id => {
      if (isFinishedTask(tasks.value[id])) return;
      try {
        const value = await deps.fetchAgentTask(id);
        if (!disposed && epoch === generation) tasks.value = { ...tasks.value, [id]: value };
      } catch (cause) {
        if (!disposed && epoch === generation) taskError.value = cause instanceof Error ? cause.message : "任务进度读取失败";
      }
    }));
  }

  async function refreshProgress() {
    if (trackingBusy || busy.value || loading.value || disposed || plansNeedReload.value) return;
    trackingBusy = true;
    const epoch = generation;
    const sessionId = session.value?.session_id;
    try {
      await refreshTasks();
      if (disposed || epoch !== generation || busy.value || !sessionId) return;
      const executions = session.value?.executions ?? [];
      // Ordinary progress reads leave the bounded plan observation budget for result verification.
      const pendingVerification = executions.find(item => item.steps.some(step =>
        step.status === "waiting_task" && step.task_id !== null && isFinishedTask(tasks.value[step.task_id]),
      ));
      const current = pendingVerification
        ?? executions.find(item => item.status === "ready" && autoContinue.has(item.plan_id));
      if (!current) return;
      const planId = current.plan_id;
      if (pendingVerification) await controlPlan(planId, "refresh");
      if (disposed || plansNeedReload.value || session.value?.session_id !== sessionId || !autoContinue.has(planId)) return;
      const plan = session.value.plans.find(item => item.plan_id === planId);
      const execution = session.value.executions.find(item => item.plan_id === planId);
      if (plan?.status === "approved" && execution?.status === "ready") await controlPlan(planId, "advance");
      if (["completed", "blocked", "cancelled"].includes(execution?.status ?? "")) autoContinue.delete(planId);
    } finally { trackingBusy = false; }
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
    autoContinue.clear();
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
        const value = await deps.fetchAgentSession(saved);
        if (disposed || epoch !== generation) return;
        if (session.value?.session_id === saved) saveSession(value, epoch);
        else openSavedSession(value, epoch);
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
    contextWindowed.value = false;
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
        if (event.context_windowed) contextWindowed.value = true;
        else if (event.type === "text" && typeof event.text === "string") draft.value += event.text;
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
      if (knownId) {
        plansNeedReload.value = true;
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
      const action = session.value?.actions.find(item => item.action_id === actionId);
      if (decision === "confirm" && action?.plan_id && action.task_id) autoContinue.add(action.plan_id);
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
      if (epoch === generation && !plansNeedReload.value) {
        if (operation === "advance") autoContinue.add(planId);
        if (operation === "cancel") autoContinue.delete(planId);
      }
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
    autoContinue.clear();
    loading.value = false;
    session.value = null;
    plansNeedReload.value = false;
    error.value = null;
    regularDraft = "";
    input.value = "";
    tasks.value = {};
    streamSessionId = null;
    pendingMessage.value = "";
    draft.value = "";
    streamEvents.value = [];
    streamIncomplete.value = false;
    contextWindowed.value = false;
    try { sessionStorage.removeItem(storageKey); } catch { /* memory-only mode */ }
  }

  async function restore(id: string) {
    if (busy.value || disposed) return;
    const epoch = ++generation;
    loading.value = true;
    error.value = null;
    try {
      if (openSavedSession(await deps.fetchAgentSession(id), epoch)) await refreshTasks();
    } catch (cause) {
      if (epoch === generation && !disposed) error.value = cause instanceof Error ? cause.message : "会话恢复失败";
    } finally { if (epoch === generation && !disposed) loading.value = false; }
  }

  async function fork(messageIndex: number) {
    const source = session.value;
    const message = source?.messages[messageIndex];
    if (busy.value || loading.value || disposed || input.value.trim() || !source || message?.role !== "user") return false;
    const epoch = ++generation;
    loading.value = true;
    error.value = null;
    try {
      const branch = await deps.forkAgentSession(source.session_id, messageIndex);
      if (!openSavedSession(branch, epoch, message.content)) return false;
      mode.value = "chat";
      return true;
    } catch (cause) {
      if (epoch === generation && !disposed) error.value = cause instanceof Error ? cause.message : "创建分支失败";
      return false;
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

  return { session, status, tasks, input, mode, streamEvents, draft, pendingMessage, streamIncomplete, contextWindowed, connected, stopResponse, plansNeedReload, busy, executionBusy, loading, error, taskError, pendingActions, sessions, historyLoading, historyError, refreshHistory, memories, restore, fork, remember, forget, load, send, decide, decidePlan, controlPlan, recoverPlanStep, reset, refreshTasks, refreshProgress, canWrite, dispose: () => { disposed = true; generation += 1; stopWatchingDraft(); streamController?.abort(); } };
}
