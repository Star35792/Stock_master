export interface AgentSkillIdentity { name: string; content_hash: string }
export interface AgentSkillVerification {
  skill: AgentSkillIdentity;
  status: "ready_to_explain" | "needs_evidence" | "needs_input";
  task_check_complete: boolean;
  goal_verified: false;
  observed_at: number;
  missing_checks: string[];
  evidence_refs: Array<Record<string, unknown>>;
  limitations: string[];
}

export interface AgentAction {
  action_id: string;
  tool: string;
  title: string;
  parameters: Record<string, unknown>;
  preview: Record<string, unknown>;
  status: "pending" | "starting" | "confirmed" | "cancelled" | "expired";
  task_id: number | null;
  plan_id: string | null;
  step_id: string | null;
  permission_scope: string | null;
}

export interface AgentSession {
  session_id: string;
  messages: Array<{ role: string; content: string }>;
  steps: Array<{ tool: string; title: string; result: unknown }>;
  actions: AgentAction[];
  plans: AgentPlan[];
  executions: AgentExecution[];
  skill_checks?: AgentSkillVerification[];
  forked_from?: { session_id: string; message_index: number } | null;
}

export interface AgentExecution {
  skill_checks?: AgentSkillVerification[];
  plan_id: string;
  version: number;
  plan_hash: string;
  status: "ready" | "running" | "awaiting_confirmation" | "waiting_task" | "completed" | "blocked" | "cancelled";
  reason: string;
  goal_verified: false;
  dispatches: number;
  advances: number;
  deadline: number;
  steps: Array<{ step_id: string; status: string; task_id: number | null; action_id: string | null; summary: Record<string, unknown>; reason: string; attempts: number; read_checkpoint?: Record<string, unknown> | null }>;
}

export interface AgentPlan {
  skills?: AgentSkillIdentity[];
  plan_id: string;
  version: number;
  plan_hash: string;
  code_version: string;
  created_at: number;
  expires_at: number;
  status: "needs_input" | "draft" | "approved" | "rejected" | "superseded" | "expired";
  intent: "data_maintenance" | "stock_research" | "position_analysis" | "portfolio_planning" | "consultation";
  goal: string;
  assumptions: string[];
  data_sources: string[];
  completion_condition: string;
  clarification_questions: string[];
  missing_inputs: string[];
  steps: Array<{
    step_id: string; tool: string; purpose: string; parameters: Record<string, unknown>;
    input_evidence: Record<string, string>; depends_on: string[];
    bindings: Array<{ parameter: string; source_step: string; output: string }>;
    completion_condition: string;
  }>;
}

export interface AgentStatus {
  skills?: Array<{ name: string; description: string }>;
  configured: boolean;
  write_enabled: boolean;
  data_maintenance_enabled?: boolean;
  model: string;
  tools: Array<{ name: string; title: string; requires_confirmation: boolean; write_enabled?: boolean }>;
  planning_available: boolean;
  plan_execution_enabled: boolean;
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
export const forkAgentSession = (id: string, message_index: number) =>
  request<AgentSession>(`/sessions/${encodeURIComponent(id)}/fork`, { message_index });
export const sendAgentMessage = (message: string, session_id: string | null) => request<AgentSession>("/chat", { message, session_id });
export const createAgentPlan = (message: string, session_id: string | null) => request<AgentSession>("/plans", { message, session_id });
export const decideAgentPlan = (plan: AgentPlan, session_id: string, decision: "approve" | "reject") => request<AgentSession>(`/plans/${encodeURIComponent(plan.plan_id)}/decision`, { session_id, version: plan.version, plan_hash: plan.plan_hash, decision });
export const controlAgentPlan = (plan: AgentPlan, session_id: string, operation: "advance" | "refresh" | "cancel") => request<AgentSession>(`/plans/${encodeURIComponent(plan.plan_id)}/execution`, { session_id, version: plan.version, plan_hash: plan.plan_hash, operation });
export const recoverAgentPlanStep = (plan: AgentPlan, session_id: string, step_id: string) => request<AgentSession>(`/plans/${encodeURIComponent(plan.plan_id)}/recovery`, { session_id, version: plan.version, plan_hash: plan.plan_hash, step_id });
export const decideAgentAction = (actionId: string, session_id: string, decision: "confirm" | "cancel") => request<AgentSession>(`/actions/${encodeURIComponent(actionId)}/decision`, { session_id, decision });
export const fetchAgentTask = (id: number) => request<AgentTask>(`/tasks/${id}`);
export const fetchAgentSessions = () => request<AgentSessionSummary[]>("/sessions");
export const fetchAgentMemories = () => request<AgentMemory[]>("/memories");
export const saveAgentMemory = (memory: { kind: AgentMemory["kind"]; content: string; source_session_id: string | null; id?: string; revision?: number }) => request<AgentMemory[]>("/memories", memory);
export const deleteAgentMemory = (id: string, revision: number) => request<AgentMemory[]>(`/memories/${encodeURIComponent(id)}?revision=${revision}`, undefined, "DELETE");
