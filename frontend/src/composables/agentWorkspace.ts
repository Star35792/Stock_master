import { computed, ref } from "vue";

import {
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

const defaultDeps = {
  fetchAgentStatus, fetchAgentSession, fetchAgentTask, sendAgentMessage, decideAgentAction,
  fetchAgentSessions, fetchAgentMemories, saveAgentMemory, deleteAgentMemory,
};

export function createAgentWorkspace(overrides: Partial<typeof defaultDeps> = {}) {
  const deps = { ...defaultDeps, ...overrides };
  const session = ref<AgentSession | null>(null);
  const status = ref<Awaited<ReturnType<typeof fetchAgentStatus>> | null>(null);
  const tasks = ref<Record<number, AgentTask>>({});
  const input = ref("");
  const busy = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const taskError = ref<string | null>(null);
  const sessions = ref<AgentSessionSummary[]>([]);
  const memories = ref<AgentMemory[]>([]);
  const pendingActions = computed(() => session.value?.actions.filter(action => action.status === "pending") ?? []);
  const storageKey = "stock-master-agent-session";
  let disposed = false;
  let generation = 0;

  function saveSession(value: AgentSession, epoch = generation) {
    if (disposed || epoch !== generation) return;
    session.value = value;
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

  async function load() {
    if (busy.value || disposed) return;
    const epoch = ++generation;
    loading.value = true;
    error.value = null;
    try {
      const value = await deps.fetchAgentStatus();
      if (disposed || epoch !== generation) return;
      status.value = value;
      const [savedSessions, savedMemories] = await Promise.all([deps.fetchAgentSessions(), deps.fetchAgentMemories()]);
      if (disposed || epoch !== generation) return;
      sessions.value = savedSessions;
      memories.value = savedMemories;
      let saved: string | null = null;
      try { saved = sessionStorage.getItem(storageKey); } catch { /* memory-only mode */ }
      if (saved && !disposed) {
        saveSession(await deps.fetchAgentSession(saved), epoch);
        if (disposed || epoch !== generation) return;
        await refreshTasks();
      }
    } catch (cause) {
      if (!disposed && epoch === generation) error.value = cause instanceof Error ? cause.message : "智能体连接失败";
    } finally { if (!disposed && epoch === generation) loading.value = false; }
  }

  async function send(message = input.value) {
    const text = message.trim();
    if (busy.value || loading.value || !text || !status.value?.configured || disposed) return;
    busy.value = true;
    error.value = null;
    try {
      saveSession(await deps.sendAgentMessage(text, session.value?.session_id ?? null));
      input.value = "";
      await refreshTasks();
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "智能体请求失败";
    } finally { busy.value = false; }
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

  function reset() {
    if (busy.value) return;
    generation += 1;
    loading.value = false;
    session.value = null;
    error.value = null;
    input.value = "";
    tasks.value = {};
    try { sessionStorage.removeItem(storageKey); } catch { /* memory-only mode */ }
  }

  async function restore(id: string) {
    if (busy.value) return;
    const epoch = ++generation;
    loading.value = true;
    error.value = null;
    try {
      saveSession(await deps.fetchAgentSession(id), epoch);
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

  return { session, status, tasks, input, busy, loading, error, taskError, pendingActions, sessions, memories, restore, remember, forget, load, send, decide, reset, refreshTasks, dispose: () => { disposed = true; generation += 1; } };
}
