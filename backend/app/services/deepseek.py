"""Small bounded DeepSeek transport; credentials and upstream bodies never leave here."""

import json
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.config.settings import Settings, settings


class AgentUnavailableError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekClient:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def complete(self, messages: list[dict], tools: list[dict], *, max_tokens: int = 1536) -> dict:
        key = self.config.deepseek_api_key
        if key is None or not key.get_secret_value().strip():
            raise AgentUnavailableError("请在 backend/.env 配置 DEEPSEEK_API_KEY 后重启后端。")
        body = json.dumps(
            {
                "model": self.config.deepseek_model,
                "messages": messages,
                "tools": tools,
                "thinking": {"type": "disabled"},
                "max_tokens": max_tokens,
                "stream": False,
            }
        ).encode()
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect).open(
                request,
                timeout=self.config.deepseek_timeout_seconds,
            ) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise AgentUnavailableError("模型响应超过大小限制，请缩小问题范围。")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
                raise ValueError
            choice = payload["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError
            if choice.get("finish_reason") == "length":
                raise AgentUnavailableError("模型输出达到上限，请缩小问题范围后重试。")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError
            return message
        except HTTPError as exc:
            hints = {401: "密钥无效", 402: "账户余额不足", 429: "请求过于频繁"}
            raise AgentUnavailableError(
                f"DeepSeek 暂不可用：{hints.get(exc.code, '上游请求失败')}（{exc.code}）。"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise AgentUnavailableError("无法连接 DeepSeek，请检查网络或稍后重试。") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise AgentUnavailableError("DeepSeek 返回了无法解析的响应。") from None

    def stream_complete(self, messages, tools, *, on_text, check, max_tokens=4096):
        """Forward public content deltas; assemble tool arguments before any dispatch."""
        key = self.config.deepseek_api_key
        if key is None or not key.get_secret_value().strip():
            raise AgentUnavailableError("请在 backend/.env 配置 DEEPSEEK_API_KEY 后重启后端。")
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(
                {
                    "model": self.config.deepseek_model,
                    "messages": messages,
                    "tools": tools,
                    "thinking": {"type": "disabled"},
                    "max_tokens": max_tokens,
                    "stream": True,
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        content, calls, total, finished, done = "", {}, 0, None, False
        deadline = monotonic() + 90
        try:
            check()
            with build_opener(_NoRedirect).open(
                request, timeout=self.config.deepseek_timeout_seconds
            ) as response:
                while True:
                    check()
                    if monotonic() >= deadline:
                        raise AgentUnavailableError("模型本轮响应超时，请载入原会话核对。")
                    line = response.readline(65537)
                    total += len(line)
                    if len(line) > 65536 or total > 1048576:
                        raise AgentUnavailableError("模型流式响应超过大小限制。")
                    if not line:
                        break
                    if not line.startswith(b"data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == b"[DONE]":
                        done = True
                        break
                    payload = json.loads(raw)
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or len(choices) != 1:
                        raise ValueError
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    if not isinstance(text, str):
                        raise ValueError
                    if text:
                        content += text
                        if len(content) > 10000:
                            raise AgentUnavailableError("模型文字超过长度限制。")
                        on_text(text)
                    fragments = delta.get("tool_calls") or []
                    if not isinstance(fragments, list):
                        raise ValueError
                    for fragment in fragments:
                        index = fragment.get("index")
                        if type(index) is not int or not 0 <= index < 4:
                            raise ValueError
                        call = calls.setdefault(
                            index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        function = fragment.get("function") or {}
                        for target, source, field in (
                            (call, fragment, "id"),
                            (call["function"], function, "name"),
                            (call["function"], function, "arguments"),
                        ):
                            piece = source.get(field) or ""
                            if not isinstance(piece, str):
                                raise ValueError
                            target[field] += piece
                        if (
                            len(call["id"]) > 100
                            or len(call["function"]["name"]) > 80
                            or len(call["function"]["arguments"]) > 12000
                        ):
                            raise ValueError
                    if choice.get("finish_reason") is not None:
                        finished = choice["finish_reason"]
            if not done or finished not in {"stop", "tool_calls"}:
                raise AgentUnavailableError("模型响应未完整结束，已收到文字仅作未完成草稿。")
            if (finished == "tool_calls") != bool(calls):
                raise ValueError
            return {"content": content, "tool_calls": [calls[i] for i in sorted(calls)]}
        except HTTPError as exc:
            hints = {401: "密钥无效", 402: "账户余额不足", 429: "请求过于频繁"}
            raise AgentUnavailableError(
                f"DeepSeek 暂不可用：{hints.get(exc.code, '上游请求失败')}（{exc.code}）。"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise AgentUnavailableError("模型连接中断或超时，请载入原会话核对。") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, UnicodeError):
            raise AgentUnavailableError("DeepSeek 返回了无法解析的流式响应。") from None
