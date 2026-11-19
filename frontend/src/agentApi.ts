export interface AgentAction {
  action_id: string;
  tool: string;
  title: string;
  parameters: Record<string, unknown>;
  preview: Record<string, unknown>;
  status: "pending" | "starting" | "confirmed" | "cancelled" | "expired";
  task_id: number | null;
}

export interface AgentSession {
  session_id: string;
  messages: Array<{ role: string; content: string }>;
  steps: Array<{ tool: string; title: string; result: unknown }>;
  actions: AgentAction[];
}

export interface AgentStatus {
  configured: boolean;
  write_enabled: boolean;
  model: string;
  tools: Array<{ name: string; title: string; requires_confirmation: boolean }>;
}

export interface AgentTask {
  task_id?: number;
  action_id?: number;
  status: "queued" | "running" | "success" | "partial" | "failed";
  phase: string;
  result: Record<string, unknown> | null;
  error_message: string | null;
}

export interface AgentMemory {
  id: string;
  kind: "preference" | "constraint" | "lesson";
  content: string;
  source_session_id: string | null;
  revision: number;
  updated_at: string;
}

export interface AgentSessionSummary { session_id: string; title: string; updated_at: string }

async function request<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const response = await fetch(`/api/v1/agent${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败（${response.status}）`);
  return data as T;
}

export const fetchAgentStatus = () => request<AgentStatus>("/status");
export const fetchAgentSession = (id: string) => request<AgentSession>(`/sessions/${encodeURIComponent(id)}`);
export const sendAgentMessage = (message: string, session_id: string | null) => request<AgentSession>("/chat", { message, session_id });
export const decideAgentAction = (actionId: string, session_id: string, decision: "confirm" | "cancel") => request<AgentSession>(`/actions/${encodeURIComponent(actionId)}/decision`, { session_id, decision });
export const fetchAgentTask = (id: number) => request<AgentTask>(`/tasks/${id}`);
export const fetchAgentSessions = () => request<AgentSessionSummary[]>("/sessions");
export const fetchAgentMemories = () => request<AgentMemory[]>("/memories");
export const saveAgentMemory = (memory: { kind: AgentMemory["kind"]; content: string; source_session_id: string | null; id?: string; revision?: number }) => request<AgentMemory[]>("/memories", memory);
export const deleteAgentMemory = (id: string, revision: number) => request<AgentMemory[]>(`/memories/${encodeURIComponent(id)}?revision=${revision}`, undefined, "DELETE");
