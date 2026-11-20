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
  const dependencies = {
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
  };
  dependencies.streamAgentRequest ??= async (mode, message, id) => (mode === "plan" ? dependencies.createAgentPlan : dependencies.sendAgentMessage)(message, id);
  const state = createAgentWorkspace(dependencies);
  state.mode.value = "chat";
  return state;
}

const planSession = (status = "draft", version = 1) => ({ ...session(), actions: [], plans: [{
  plan_id: `plan-${version}`, version, plan_hash: "a".repeat(64), status, goal: "检查覆盖", steps: [],
  missing_inputs: status === "needs_input" ? ["book_id"] : [], clarification_questions: [],
}] });

test("queries are the default and explicit planning remains separate", async () => {
  expect(createAgentWorkspace().mode.value).toBe("chat");
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

test("history refresh discovers new conversations without changing the active response", async () => {
  let saved = [];
  const state = workspace({ fetchAgentSessions: async () => saved });
  await state.load();
  await state.send("当前需求");
  state.draft.value = "尚未完成的答复";
  state.streamIncomplete.value = true;
  saved = [{ session_id: "session-1", title: "当前需求", updated_at: "2026-11-20" }];
  await state.refreshHistory();
  expect(state.sessions.value).toEqual(saved);
  expect(state.session.value.session_id).toBe("session-1");
  expect(state.draft.value).toBe("尚未完成的答复");
  expect(state.streamIncomplete.value).toBe(true);
});

test("history errors remain separate from the current conversation and can be retried", async () => {
  let fails = false;
  const state = workspace({ fetchAgentSessions: async () => { if (fails) throw new Error("历史暂不可用"); return []; } });
  await state.load();
  fails = true;
  await state.refreshHistory();
  expect(state.historyError.value).toBe("历史暂不可用");
  expect(state.error.value).toBeNull();
  expect(state.historyLoading.value).toBe(false);
  fails = false;
  await state.refreshHistory();
  expect(state.historyError.value).toBeNull();
});

test("late history refresh cannot repopulate state after a new conversation", async () => {
  let release;
  const state = workspace({ fetchAgentSessions: () => new Promise(resolve => { release = resolve; }) });
  const pending = state.refreshHistory();
  await state.refreshHistory();
  state.reset();
  release([{ session_id: "old", title: "旧对话" }]);
  await pending;
  expect(state.sessions.value).toEqual([]);
  expect(state.historyLoading.value).toBe(false);
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

function trackedSession(status = "waiting_task", stepStatus = "waiting_task") {
  return { ...planSession("approved"), actions: [{ ...session().actions[0], plan_id: "plan-1", task_id: 71, status: "confirmed" }],
    executions: [{ plan_id: "plan-1", status, steps: [{ step_id: "collect", status: stepStatus, task_id: 71 }] }] };
}

const taskProgress = status => ({ task_id: 71, status, phase: status, result: null, error_message: null });

test("maintenance scope enables only its own confirmation buttons", async () => {
  const state = workspace({ fetchAgentStatus: async () => ({ configured: true, write_enabled: false, data_maintenance_enabled: true,
    tools: [{ name: "sync_daily_data", write_enabled: true }, { name: "run_factor_backtest", write_enabled: false }] }) });
  await state.load();
  expect(state.canWrite("sync_daily_data")).toBe(true);
  expect(state.canWrite("run_factor_backtest")).toBe(false);
  expect(state.canWrite("execute_sql")).toBe(false);
});

test("explicit run tracks the original task and continues to the next preflight without confirming", async () => {
  const calls = [];
  let confirms = 0;
  let taskStatus = "queued";
  const state = workspace({ fetchAgentSession: async () => planSession("approved"),
    fetchAgentTask: async () => taskProgress(taskStatus),
    decideAgentAction: async () => { confirms += 1; return session(); },
    controlAgentPlan: async (_plan, _id, operation) => {
      calls.push(operation);
      if (operation === "refresh") {
        return taskStatus === "success" ? trackedSession("ready", "success") : trackedSession();
      }
      return calls.length === 1 ? trackedSession() : trackedSession("awaiting_confirmation", "awaiting_confirmation");
    } });
  await state.load(); await state.restore("session-1");
  await state.controlPlan("plan-1", "advance");
  await state.refreshProgress();
  expect(state.tasks.value[71].status).toBe("queued");
  taskStatus = "running";
  for (let i = 0; i < 256; i += 1) await state.refreshProgress();
  expect(state.tasks.value[71].status).toBe("running");
  expect(calls).toEqual(["advance"]);

  taskStatus = "success";
  await state.refreshProgress();
  await state.refreshProgress();
  expect(calls).toEqual(["advance", "refresh", "advance"]);
  expect(state.session.value.executions[0].status).toBe("awaiting_confirmation");
  expect(confirms).toBe(0);
});

test("restoring a task observes it without automatically advancing the plan", async () => {
  const calls = [];
  const state = workspace({ fetchAgentSession: async () => trackedSession(),
    fetchAgentTask: async () => taskProgress("success"), controlAgentPlan: async (_p, _s, operation) => {
    calls.push(operation); return trackedSession("ready", "success");
  } });
  await state.load(); await state.restore("session-1");
  await state.refreshProgress();
  expect(calls).toEqual(["refresh"]);
});

test("partial task observation blocks automatic downstream work", async () => {
  const calls = [];
  const state = workspace({ fetchAgentSession: async () => planSession("approved"),
    fetchAgentTask: async () => taskProgress("partial"), controlAgentPlan: async (_p, _s, operation) => {
    calls.push(operation); return operation === "advance" ? trackedSession() : trackedSession("blocked", "partial");
  } });
  await state.load(); await state.restore("session-1");
  await state.controlPlan("plan-1", "advance");
  await state.refreshProgress(); await state.refreshProgress();
  expect(calls).toEqual(["advance", "refresh"]);
});

test("a temporary task progress error can recover without consuming a plan observation", async () => {
  const calls = [];
  let unavailable = true;
  const state = workspace({
    fetchAgentSession: async () => trackedSession(),
    fetchAgentTask: async () => {
      if (unavailable) throw new Error("连接中断");
      return taskProgress("failed");
    },
    controlAgentPlan: async (_p, _s, operation) => {
      calls.push(operation);
      return trackedSession("blocked", "failed");
    },
  });
  await state.load(); await state.restore("session-1");
  await state.refreshProgress();
  expect(state.taskError.value).toBe("连接中断");
  expect(calls).toEqual([]);

  unavailable = false;
  await state.refreshProgress();
  expect(state.taskError.value).toBeNull();
  expect(calls).toEqual(["refresh"]);
});

test("reconnecting after an uncertain plan response observes the task without resuming the plan", async () => {
  const calls = [];
  const state = workspace({
    fetchAgentSession: async () => trackedSession(),
    fetchAgentTask: async () => taskProgress("success"),
    controlAgentPlan: async (_p, _s, operation) => {
      calls.push(operation);
      if (calls.length === 1) return trackedSession();
      if (calls.length === 2) throw new Error("连接中断");
      return trackedSession("ready", "success");
    },
  });
  await state.load(); await state.restore("session-1");
  await state.controlPlan("plan-1", "advance");
  await state.refreshProgress();
  expect(state.plansNeedReload.value).toBe(true);

  await state.load();
  await state.refreshProgress();
  expect(calls).toEqual(["advance", "refresh", "refresh"]);
  expect(state.session.value.executions[0].status).toBe("ready");
});

test("resetting during a progress observation cannot resume the old plan", async () => {
  let release;
  let calls = 0;
  const state = workspace({ fetchAgentSession: async () => trackedSession(), fetchAgentTask: async () => new Promise(resolve => { release = resolve; }),
    controlAgentPlan: async () => { calls += 1; return trackedSession(); } });
  // Restore a session directly: its task card is observed by refreshProgress below.
  await state.load(); state.session.value = trackedSession();
  const pending = state.refreshProgress();
  state.reset(); release({ task_id: 71, status: "success" }); await pending;
  expect(state.session.value).toBeNull(); expect(calls).toBe(0);
});
