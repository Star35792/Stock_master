"""Canonical identity shared by business evidence without importing an executor."""

import json
from hashlib import sha256


def parameters_hash(tool, parameters):
    return sha256(
        json.dumps(
            {"tool": tool, "parameters": parameters},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
