import { describe, expect, test } from "bun:test";
import { consumeAgentStream } from "../src/agentStream";
import { createAgentWorkspace } from "../src/composables/agentWorkspace";

const finalSession = { session_id: "session-1", messages: [{ role: "user", content: "检查" }, { role: "assistant", content: "结果" }], steps: [], actions: [], plans: [], executions: [] };
function frame(type: string, sequence: number, rest = {}) { return `event: ${type}\ndata: ${JSON.stringify({ type, sequence, ...rest })}\n\n`; }
function response(parts: Uint8Array[]) {
  return new Response(new ReadableStream({ start(controller) { for (const part of parts) controller.enqueue(part); controller.close(); } }), { headers: { "Content-Type": "text/event-stream" } });
}

describe("agent SSE transport", () => {
  test("handles split UTF-8 and SSE frames and replaces draft with canonical session", async () => {
    const bytes = new TextEncoder().encode(frame("session", 1, { session_id: "session-1" }) + frame("text", 2, { text: "中文" }) + frame("done", 3, { session: finalSession }));
    const parts = Array.from(bytes, value => new Uint8Array([value]));
    const events: string[] = [];
    const result = await consumeAgentStream(response(parts), event => { if (event.text) events.push(event.text); });
    expect(events).toEqual(["中文"]);
    expect(result).toEqual(finalSession);
  });

  test("EOF without done and out of order events are errors", async () => {
    const encode = (text: string) => [new TextEncoder().encode(text)];
    await expect(consumeAgentStream(response(encode(frame("text", 1, { text: "半句" }))), () => {})).rejects.toThrow("未完成");
    await expect(consumeAgentStream(response(encode(frame("phase", 2) + frame("text", 1, { text: "半句" }))), () => {})).rejects.toThrow("顺序");
  });
});

function workspace(stream: (...args: any[]) => Promise<any>, overrides = {}) {
  const values = new Map<string, string>();
  globalThis.sessionStorage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } } as Storage;
  return createAgentWorkspace({ streamAgentRequest: stream,
    fetchAgentStatus: async () => ({ configured: true, model: "test", tools: [], write_enabled: false, planning_available: true, plan_execution_enabled: true }),
    fetchAgentSessions: async () => [], fetchAgentMemories: async () => [],
    ...overrides,
  });
}

test("forks a saved question as a draft and recovers the branch if the first stream fails", async () => {
  const branch = { ...finalSession, session_id: "branch-1", messages: [], forked_from: { session_id: "session-1", message_index: 0 } };
  const loads: string[] = [];
  const state = workspace(async (_mode, text, id) => {
    expect([text, id]).toEqual(["检查另一组", "branch-1"]);
    throw new Error("连接中断");
  }, {
    fetchAgentSession: async (id: string) => { loads.push(id); return id === "branch-1" ? branch : finalSession; },
    forkAgentSession: async (id: string, index: number) => {
      expect([id, index]).toEqual(["session-1", 0]);
      return branch;
    },
  });
  await state.load();
  await state.restore("session-1");
  expect(await state.fork(0)).toBe(true);
  expect(state.input.value).toBe("检查");
  expect(state.session.value?.messages).toEqual([]);
  state.input.value = "检查另一组";
  await state.restore("session-1");
  state.input.value = "来源会话草稿";
  await state.restore("branch-1");
  expect(state.input.value).toBe("检查另一组");
  await state.restore("session-1");
  expect(state.input.value).toBe("来源会话草稿");
  await state.restore("branch-1");
  await state.send();
  expect(loads.at(-1)).toBe("branch-1");
  expect(state.error.value).toBe("连接中断");
  expect(state.session.value?.session_id).toBe("branch-1");
  expect(state.input.value).toBe("检查另一组");
});

test("a failed fork leaves the source conversation active", async () => {
  const state = workspace(async () => finalSession, {
    fetchAgentSession: async () => finalSession,
    forkAgentSession: async () => { throw new Error("会话数已达上限"); },
  });
  await state.restore("session-1");
  expect(await state.fork(0)).toBe(false);
  expect(state.session.value).toEqual(finalSession);
  expect(state.error.value).toBe("会话数已达上限");
});

test("shows progress and draft before completion, then renders final answer once", async () => {
  let finish!: (value: any) => void;
  const state = workspace(async (_mode, _text, _id, emit) => {
    emit({ type: "session", session_id: "new-session" });
    emit({ type: "phase", context_windowed: true });
    emit({ type: "text", text: "中间草稿" });
    emit({ type: "draft_reset" });
    emit({ type: "tool_start", message: "正在读取质量问题" });
    emit({ type: "text", text: "结果" });
    return new Promise(resolve => { finish = resolve; });
  });
  await state.load();
  const pending = state.send("检查");
  expect(state.mode.value).toBe("chat");
  expect(state.contextWindowed.value).toBe(true);
  expect(state.pendingMessage.value).toBe("检查");
  expect(state.draft.value).toBe("结果");
  expect(state.streamEvents.value.some(item => item.type === "tool_start")).toBe(true);
  expect(sessionStorage.getItem("stock-master-agent-session")).toBe("new-session");
  finish(finalSession);
  await pending;
  expect(state.draft.value).toBe("");
  expect(state.pendingMessage.value).toBe("");
  expect(state.session.value?.messages.length).toBe(2);
});

test("disconnect retains incomplete draft and never resubmits automatically", async () => {
  let requests = 0;
  const state = workspace(async (_mode, _text, _id, emit) => {
    requests += 1;
    emit({ type: "session", session_id: "new-session" });
    emit({ type: "text", text: "未完成" });
    throw new Error("连接中断");
  });
  await state.load();
  await state.send("检查");
  expect(requests).toBe(1);
  expect(state.streamIncomplete.value).toBe(true);
  expect(state.draft.value).toBe("未完成");
  expect(state.input.value).toBe("");
});

test("unmounted workspace ignores late events and aborts its stream", async () => {
  let emit!: (event: any) => void;
  let finish!: (value: any) => void;
  let signal!: AbortSignal;
  const state = workspace(async (_mode, _text, _id, callback, currentSignal) => {
    emit = callback; signal = currentSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  await state.load();
  const pending = state.send("检查");
  state.dispose();
  emit({ type: "text", text: "旧结果" });
  finish(finalSession);
  await pending;
  expect(state.session.value).toBeNull();
  expect(state.draft.value).toBe("");
  expect(signal.aborted).toBe(true);
});

test("failed plan reload clears temporary bubbles and preserves incomplete status", async () => {
  let requests = 0;
  const state = workspace(async (_mode, _text, _id, emit) => {
    requests += 1;
    emit({ type: "session", session_id: "session-1" });
    emit({ type: "text", text: "未完成草稿" });
    throw new Error("连接中断");
  }, { fetchAgentSession: async () => finalSession });
  await state.load();
  state.input.value = "检查";
  await state.send(undefined, "plan");
  expect(requests).toBe(1);
  expect(state.session.value?.messages).toEqual(finalSession.messages);
  expect(state.pendingMessage.value).toBe("");
  expect(state.draft.value).toBe("");
  expect(state.input.value).toBe("");
  expect(state.streamIncomplete.value).toBe(true);
});
