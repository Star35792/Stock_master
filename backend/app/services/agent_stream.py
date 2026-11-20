"""Bounded SSE delivery around the existing synchronous agent and its CAS state."""

import asyncio
import json
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Thread
from time import monotonic

from app.services.agent_errors import AgentConflictError, AgentNotFoundError
from app.services.deepseek import AgentUnavailableError

STREAM_SLOTS = BoundedSemaphore(4)
MAX_STREAM_BYTES = 2 * 1024 * 1024


class AgentStreamStopped(RuntimeError):
    pass


class AgentStream:
    def __init__(self):
        self.queue = Queue(maxsize=128)
        self.stopped = Event()
        self.finished = Event()
        self.deadline = monotonic() + 360
        self.size = 0
        self.sequence = 0
        self.draft = ""

    def check(self):
        if self.stopped.is_set() or monotonic() >= self.deadline:
            raise AgentStreamStopped("本次响应已停止；请载入原会话核对已完成的查询。")

    def emit(self, kind, **data):
        self.check()
        self.sequence += 1
        encoded = json.dumps({"type": kind, "sequence": self.sequence, **data}, ensure_ascii=False)
        limit = 512 * 1024 if kind == "done" else 32 * 1024
        self.size += len(encoded.encode())
        if len(encoded.encode()) > limit or self.size > MAX_STREAM_BYTES:
            raise AgentStreamStopped("本次响应超过容量上限，请载入原会话查看结果。")
        frame = f"event: {kind}\ndata: {encoded}\n\n"
        while True:
            self.check()
            try:
                self.queue.put(frame, timeout=0.2)
                return
            except Full:
                continue

    def _run(self, operation, message, session_id):
        acquired = STREAM_SLOTS.acquire(blocking=False)
        try:
            if not acquired:
                raise AgentConflictError("正在处理的流式请求已达上限，请稍后重试。")
            result = operation(message, session_id, events=self)
            # Operation returns only after its final state has been saved successfully.
            self.emit("done", session=result.model_dump(mode="json"))
        except AgentStreamStopped:
            pass
        except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
            try:
                self.emit("error", message=str(exc))
            except AgentStreamStopped:
                pass
        except Exception:
            try:
                self.emit("error", message="响应处理失败，请载入原会话核对；系统不会自动重放。")
            except AgentStreamStopped:
                pass
        finally:
            if acquired:
                STREAM_SLOTS.release()
            self.finished.set()

    async def frames(self, is_disconnected, operation, message, session_id):
        worker = Thread(target=self._run, args=(operation, message, session_id), daemon=True)
        worker.start()
        last_sent = monotonic()
        try:
            while True:
                if await is_disconnected():
                    break
                if monotonic() >= self.deadline:
                    yield (
                        'event: error\ndata: {"type":"error",'
                        '"message":"响应超时，请载入原会话核对。"}\n\n'
                    )
                    break
                try:
                    frame = self.queue.get_nowait()
                except Empty:
                    if self.finished.is_set():
                        break
                    if monotonic() - last_sent >= 10:
                        yield ": keep-alive\n\n"
                        last_sent = monotonic()
                    await asyncio.sleep(0.05)
                    continue
                yield frame
                last_sent = monotonic()
        finally:
            self.stopped.set()
