"""Keep model context bounded without modifying the visible conversation."""

import json

HISTORY_BYTES = 48_000


def context_size(value: dict | list[dict]) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def recent_turns(messages: list[dict], budget: int = HISTORY_BYTES) -> list[dict]:
    """Take a suffix starting at a user message, keeping tool calls and results together."""
    if not messages or budget < 2:
        return []
    end = len(messages)
    start = end
    remaining = budget
    for index in range(end - 1, -1, -1):
        if messages[index]["role"] != "user":
            continue
        turn_size = context_size(messages[index:end])
        if turn_size > remaining:
            break
        remaining -= turn_size
        end = start = index
    return messages[start:]
