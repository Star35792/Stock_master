import type { AgentSession } from "./agentApi";

export interface AgentStreamEvent {
  type: "session" | "phase" | "round" | "text" | "draft_reset" | "progress" | "tool_start" | "tool_end" | "done" | "error";
  sequence?: number;
  session_id?: string;
  session?: AgentSession;
  text?: string;
  message?: string;
  context_windowed?: boolean;
  title?: string;
  tool?: string;
  parameters?: Record<string, unknown>;
  result?: unknown;
}

const eventTypes = new Set(["session", "phase", "round", "text", "draft_reset", "progress", "tool_start", "tool_end", "done", "error"]);

export async function consumeAgentStream(response: Response, onEvent: (event: AgentStreamEvent) => void, signal?: AbortSignal): Promise<AgentSession> {
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败（${response.status}）`);
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream") || !response.body) throw new Error("服务端未返回流式响应，请重新连接。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let bytes = 0;
  let sequence = 0;
  try {
    while (true) {
      if (signal?.aborted) throw new Error("已停止接收；已开始的查询请载入原会话核对。");
      const { value, done } = await reader.read();
      if (value) bytes += value.byteLength;
      if (bytes > 2 * 1024 * 1024) throw new Error("响应超过大小限制，请载入原会话查看。");
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const event = JSON.parse(data) as AgentStreamEvent;
        if (!event || !eventTypes.has(event.type)) throw new Error("收到无法识别的响应事件。");
        if (event.sequence !== undefined) {
          if (!Number.isInteger(event.sequence) || event.sequence <= sequence) throw new Error("响应事件顺序异常，请载入原会话核对。");
          sequence = event.sequence;
        }
        onEvent(event);
        if (event.type === "error") throw new Error(event.message || "响应未完成，请载入原会话核对。");
        if (event.type === "done") {
          if (!event.session?.session_id || !Array.isArray(event.session.messages)) throw new Error("最终会话结果无效，请重新载入。");
          return event.session;
        }
      }
      if (done) break;
      if (buffer.length > 512 * 1024) throw new Error("响应事件超过大小限制。");
    }
    throw new Error("连接已结束，答复未完成；请载入原会话核对，系统不会自动重发。");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function streamAgentRequest(mode: "plan" | "chat", message: string, session_id: string | null, onEvent: (event: AgentStreamEvent) => void, signal?: AbortSignal) {
  const response = await fetch(`/api/v1/agent/${mode === "plan" ? "plans" : "chat"}/stream`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, session_id }), signal,
  });
  return consumeAgentStream(response, onEvent, signal);
}
