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
  return createAgentWorkspace({
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
}

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
