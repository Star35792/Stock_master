"""Small bounded DeepSeek transport; credentials and upstream bodies never leave here."""

import json
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

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self.config.deepseek_api_key
        if key is None or not key.get_secret_value().strip():
            raise AgentUnavailableError("请在 backend/.env 配置 DEEPSEEK_API_KEY 后重启后端。")
        body = json.dumps(
            {
                "model": self.config.deepseek_model,
                "messages": messages,
                "tools": tools,
                "thinking": {"type": "disabled"},
                "max_tokens": 1536,
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
