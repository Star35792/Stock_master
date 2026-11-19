"""Canonical identities and bounded summaries shared by execution adapters."""

import json
from hashlib import sha256


def digest(value):
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def parameters_hash(tool, parameters):
    from app.utils.agent_identity import parameters_hash as canonical_parameters_hash

    return canonical_parameters_hash(tool, parameters)


def compact_preview(value):
    """Keep the review contract, not a second copy of portfolio holdings or gap rows."""
    from app.services.agent_tools import json_ready

    value = json_ready(value)

    def compact(item):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key == "_agent_grant":
                    continue
                if key == "checks" and isinstance(child, list):
                    if len(child) > 32:
                        raise ValueError("预检诊断项过多，请到原页面审阅。")
                    result[key] = []
                    for check in child:
                        if not isinstance(check, dict):
                            raise ValueError("预检诊断格式无效。")
                        result[key].append(
                            {
                                field: value
                                for field, value in check.items()
                                if field
                                in {
                                    "code",
                                    "title",
                                    "status",
                                    "passed",
                                    "message",
                                    "action",
                                    "severity",
                                    "name",
                                    "detail",
                                    "next_action",
                                }
                                and isinstance(value, (str, bool, int))
                                and len(str(value)) <= 600
                            }
                        )
                    continue
                if isinstance(child, list) and key not in {"warnings", "errors"}:
                    result[key + "_count"] = len(child)
                    result[key + "_sha256"] = digest(child)
                else:
                    result[key] = compact(child)
            return result
        if isinstance(item, list):
            return [compact(child) for child in item]
        return item

    result = compact(value)
    if not isinstance(result, dict) or len(json.dumps(result).encode()) > 8192:
        raise ValueError("预检摘要超过容量上限，请到原页面缩小范围。")
    return result


def preview_hash(preview):
    def stable(value):
        if isinstance(value, dict):
            return {
                key: stable(child)
                for key, child in value.items()
                if key not in {"_agent_grant", "generated_at", "checked_at", "previewed_at"}
            }
        if isinstance(value, list):
            return [stable(child) for child in value]
        return value

    return digest(stable(preview))


def result_summary(value):
    """Bounded scalar facts and counts; details stay in the original business store."""
    from app.services.agent_tools import json_ready

    value = json_ready(value)
    result = {"note": "步骤调用结果摘要，业务目标与证据资格仍待核验。"}
    if isinstance(value, dict):
        for key in (
            "symbol",
            "trade_date",
            "as_of_date",
            "run_id",
            "task_id",
            "book_id",
            "status",
            "phase",
            "research_only",
            "evidence_qualified",
            "total_equity",
        ):
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)) and len(str(item)) <= 200:
                result[key] = item
        result["collections"] = {
            key: len(item) for key, item in list(value.items())[:12] if isinstance(item, list)
        }
        result["unavailable_sections"] = [
            key for key, item in list(value.items())[:24] if key.endswith("_error") and item
        ]
    elif isinstance(value, list):
        result["item_count"] = len(value)
    return result
