import { beforeEach, expect, test } from "bun:test";

import { createAgentWorkspace } from "../src/composables/agentWorkspace.ts";

beforeEach(() => {
  const values = new Map();
  globalThis.sessionStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
});

const session = (id = "session-1") => ({
  session_id: id, messages: [{ role: "assistant", content: "研究结果" }], steps: [],
  actions: [{ action_id: "action-1", title: "采集", parameters: {}, preview: {}, status: "pending", task_id: null }],
});

function workspace(overrides = {}) {
  const state = createAgentWorkspace({
    createAgentPlan: async () => session(),
    fetchAgentStatus: async () => ({ configured: true, model: "test", tools: [] }),
    fetchAgentSession: async id => session(id),
    fetchAgentSessions: async () => [],
    fetchAgentMemories: async () => [],
    saveAgentMemory: async value => [{ id: "memory-1", revision: 1, ...value }],
    deleteAgentMemory: async () => [],
    fetchAgentTask: async id => ({ task_id: id, status: "running", phase: "running", result: null, error_message: null }),
    sendAgentMessage: async () => session(),
    decideAgentAction: async () => ({ ...session(), actions: [{ ...session().actions[0], status: "confirmed", task_id: 71 }] }),
    ...overrides,
  });
  state.mode.value = "chat";
  return state;
}

const planSession = (status = "draft", version = 1) => ({ ...session(), actions: [], plans: [{
  plan_id: `plan-${version}`, version, plan_hash: "a".repeat(64), status, goal: "检查覆盖", steps: [],
  missing_inputs: status === "needs_input" ? ["book_id"] : [], clarification_questions: [],
}] });

test("planning is the default and does not dispatch legacy chat or confirmation", async () => {
  expect(createAgentWorkspace().mode.value).toBe("plan");
  let plans = 0;
  let chats = 0;
  const state = workspace({ createAgentPlan: async () => { plans += 1; return planSession(); }, sendAgentMessage: async () => { chats += 1; return session(); } });
  state.mode.value = "plan";
  await state.load();
  await state.send("检查覆盖");
  expect(plans).toBe(1);
  expect(chats).toBe(0);
  expect(state.session.value.plans[0].status).toBe("draft");
});

test("task follow-up explicitly uses chat while planning remains selected", async () => {
  let chats = 0;
  let plans = 0;
  const state = workspace({ sendAgentMessage: async () => { chats += 1; return session(); }, createAgentPlan: async () => { plans += 1; return planSession(); } });
  state.mode.value = "plan";
  await state.load();
  await state.send("查询任务71的状态", "chat");
  expect(chats).toBe(1);
  expect(plans).toBe(0);
  expect(state.mode.value).toBe("plan");
});

test("clarification keeps the session and approval submits its reviewed version once", async () => {
  let generation = 0;
  let release;
  let submitted;
  let count = 0;
  const state = workspace({
    createAgentPlan: async (_message, id) => { generation += 1; if (generation === 2) expect(id).toBe("session-1"); return planSession(generation === 1 ? "needs_input" : "draft", generation); },
    decideAgentPlan: (plan, id, decision) => { count += 1; submitted = { plan, id, decision }; return new Promise(resolve => { release = resolve; }); },
  });
  state.mode.value = "plan";
  await state.load();
  await state.send("分析持仓");
  expect(state.session.value.plans[0].status).toBe("needs_input");
  await state.send("book_id=7");
  const pending = state.decidePlan("plan-2", "approve");
  await state.decidePlan("plan-2", "approve");
  expect(count).toBe(1);
  expect(submitted.plan.version).toBe(2);
  expect(submitted.plan.plan_hash).toBe("a".repeat(64));
  release(planSession("approved", 2));
  await pending;
  expect(state.session.value.plans[0].status).toBe("approved");
  expect(state.tasks.value).toEqual({});
});

test("failed replan preserves the answer and reloads superseded approval", async () => {
  const state = workspace({ createAgentPlan: async () => { throw new Error("模型暂不可用"); }, fetchAgentSession: async () => planSession("superseded") });
  state.mode.value = "plan";
  await state.load();
  state.session.value = planSession("approved");
  state.input.value = "改用其他计划";
  await state.send();
  expect(state.input.value).toBe("改用其他计划");
  expect(state.error.value).toBe("模型暂不可用");
  expect(state.session.value.plans[0].status).toBe("superseded");
});

test("approved plans restore without replaying any business action", async () => {
  let approvals = 0;
  const state = workspace({ fetchAgentSession: async () => planSession("approved"), decideAgentPlan: async () => { approvals += 1; return planSession("approved"); } });
  await state.load();
  await state.restore("session-1");
  expect(state.session.value.plans[0].status).toBe("approved");
  expect(approvals).toBe(0);
});

test("execution only starts on explicit control and duplicate advance is suppressed", async () => {
  let release;
  let calls = 0;
  const state = workspace({ fetchAgentSession: async () => planSession("approved"), controlAgentPlan: () => { calls += 1; return new Promise(resolve => { release = resolve; }); } });
  await state.load();
  await state.restore("session-1");
  expect(calls).toBe(0);
  const pending = state.controlPlan("plan-1", "advance");
  await state.controlPlan("plan-1", "advance");
  expect(calls).toBe(1);
  release({ ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "completed", goal_verified: false, steps: [] }] });
  await pending;
  expect(state.session.value.executions[0].goal_verified).toBe(false);
});

test("cancel can interrupt pending advance and its response wins over a late response", async () => {
  let release;
  const state = workspace({ fetchAgentSession: async () => planSession("approved"), controlAgentPlan: async (_plan, _id, operation) => {
    if (operation === "advance") return new Promise(resolve => { release = resolve; });
    return { ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "cancelled", steps: [] }] };
  } });
  await state.load();
  await state.restore("session-1");
  const pending = state.controlPlan("plan-1", "advance");
  await state.controlPlan("plan-1", "cancel");
  release({ ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "running", steps: [] }] });
  await pending;
  expect(state.session.value.executions[0].status).toBe("cancelled");
  expect(state.busy.value).toBe(false);
});

test("unknown execution response disables control until server state reloads", async () => {
  const state = workspace({ fetchAgentSession: async () => planSession("approved"), controlAgentPlan: async () => { throw new Error("结果待核实"); } });
  await state.load();
  await state.restore("session-1");
  await state.controlPlan("plan-1", "advance");
  expect(state.plansNeedReload.value).toBe(true);
  expect(state.error.value).toBe("结果待核实");
  await state.load();
  expect(state.plansNeedReload.value).toBe(false);
});

test("recovery inspects a frozen step without confirming or advancing it", async () => {
  let release, calls = 0, submitted;
  const state = workspace({
    fetchAgentSession: async () => planSession("approved"),
    recoverAgentPlanStep: (plan, id, stepId) => {
      calls += 1; submitted = { plan, id, stepId };
      return new Promise(resolve => { release = resolve; });
    },
    controlAgentPlan: async () => { throw new Error("must not advance"); },
    decideAgentAction: async () => { throw new Error("must not confirm"); },
  });
  await state.load(); await state.restore("session-1");
  const pending = state.recoverPlanStep("plan-1", "collect");
  await state.recoverPlanStep("plan-1", "collect");
  expect(calls).toBe(1);
  expect(submitted.stepId).toBe("collect");
  expect(submitted.plan.plan_hash).toBe("a".repeat(64));
  release({ ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "blocked", steps: [{ step_id: "collect", summary: { recovery: { status: "failed", artifact_id: 42 } } }] }] });
  await pending;
  expect(state.session.value.executions[0].status).toBe("blocked");
  expect(state.session.value.executions[0].steps[0].summary.recovery.artifact_id).toBe(42);
});

test("cancelling evidence inspection wins over a late verified response", async () => {
  let release;
  const state = workspace({
    fetchAgentSession: async () => planSession("approved"),
    recoverAgentPlanStep: () => new Promise(resolve => { release = resolve; }),
    controlAgentPlan: async () => ({ ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "cancelled", steps: [] }] }),
  });
  await state.load(); await state.restore("session-1");
  const pending = state.recoverPlanStep("plan-1", "collect");
  await state.controlPlan("plan-1", "cancel");
  release({ ...planSession("approved"), executions: [{ plan_id: "plan-1", status: "completed", steps: [] }] });
  await pending;
  expect(state.session.value.executions[0].status).toBe("cancelled");
});

test("sending creates a proposal without implicitly confirming it", async () => {
  let confirmations = 0;
  const state = workspace({ decideAgentAction: async () => { confirmations += 1; return session(); } });
  await state.load();
  await state.send("采集数据");
  expect(state.pendingActions.value).toHaveLength(1);
  expect(confirmations).toBe(0);
  expect(sessionStorage.getItem("stock-master-agent-session")).toBe("session-1");
});

test("duplicate confirmation clicks dispatch once while request is pending", async () => {
  let release;
  let count = 0;
  const state = workspace({ decideAgentAction: () => { count += 1; return new Promise(resolve => { release = resolve; }); } });
  await state.load();
  await state.send("采集");
  const pending = state.decide("action-1", "confirm");
  await state.decide("action-1", "confirm");
  expect(count).toBe(1);
  release({ ...session(), actions: [{ ...session().actions[0], status: "confirmed", task_id: 71 }] });
  await pending;
  expect(state.tasks.value[71].status).toBe("running");
});

test("late restoration cannot replace a new conversation", async () => {
  let release;
  sessionStorage.setItem("stock-master-agent-session", "old-session");
  const state = workspace({ fetchAgentSession: () => new Promise(resolve => { release = resolve; }) });
  const loading = state.load();
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
  state.reset();
  release(session("old-session"));
  await loading;
  expect(state.session.value).toBeNull();
  expect(sessionStorage.getItem("stock-master-agent-session")).toBeNull();
});

test("missing credentials fail closed and provider failure preserves typed input", async () => {
  let calls = 0;
  const state = workspace({ fetchAgentStatus: async () => ({ configured: false }), sendAgentMessage: async () => { calls += 1; throw new Error("服务暂不可用"); } });
  await state.load();
  await state.send("测试");
  expect(calls).toBe(0);
  state.status.value = { configured: true };
  state.input.value = "保留这段研究需求";
  await state.send();
  expect(state.error.value).toBe("服务暂不可用");
  expect(state.input.value).toBe("保留这段研究需求");
  expect(state.busy.value).toBe(false);
});

test("partial and failed tasks stay visible without becoming successful", async () => {
  const state = workspace({ fetchAgentTask: async id => ({ task_id: id, status: "partial", phase: "completed", result: { unresolved_rows: 3 }, error_message: null }) });
  await state.load();
  await state.send("修复");
  await state.decide("action-1", "confirm");
  expect(state.tasks.value[71].status).toBe("partial");
  expect(state.tasks.value[71].result.unresolved_rows).toBe(3);
  state.dispose();
});

test("long-term memory is only saved explicitly and can be removed", async () => {
  let saved = 0;
  const state = workspace({ saveAgentMemory: async value => { saved += 1; return [{ id: "memory-1", revision: 1, ...value }]; } });
  await state.load();
  await state.send("我偏好低波动");
  expect(saved).toBe(0);
  expect(await state.remember("preference", "优先解释日期")).toBe(true);
  expect(state.memories.value).toHaveLength(1);
  await state.forget(state.memories.value[0]);
  expect(state.memories.value).toHaveLength(0);
});

test("history restoration uses the server-owned session", async () => {
  const state = workspace();
  await state.load();
  await state.restore("saved-session");
  expect(state.session.value.session_id).toBe("saved-session");
});

test("editing a memory submits its original identity and revision", async () => {
  let payload;
  const state = workspace({ saveAgentMemory: async value => { payload = value; return [{ ...value, revision: 3 }]; } });
  await state.load();
  await state.remember("preference", "修改后的偏好", { id: "saved-memory", revision: 2 });
  expect(payload.id).toBe("saved-memory");
  expect(payload.revision).toBe(2);
  expect(state.memories.value[0].revision).toBe(3);
});
