"""Contract test: aiDoc timestamped documents carry valid status frontmatter.

The status index is generated from the frontmatter, so this test is what keeps
``AGENTS.md``'s documentation-layer rule enforceable: every ``yyyyMMdd-HHmm-``
document must declare ``doc_type`` / ``status`` / ``as_of``, and the aggregate
index must match them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AIDOC = REPO_ROOT / "aiDoc"
INDEX_PATH = AIDOC / "20261115-1649-doc-status-index.md"
GENERATOR = REPO_ROOT / "scripts" / "doc_status_index.py"

DOC_TYPES = {"decision", "plan", "evidence", "analysis", "config", "index"}
STATUSES = {"current", "snapshot", "partially_superseded", "withdrawn"}
STAMPED = re.compile(r"^20\d{6}(-\d{4})?-.*\.md$")


def timestamped_docs() -> list[Path]:
    return sorted(
        path for path in AIDOC.rglob("*.md") if STAMPED.match(path.name) and path != INDEX_PATH
    )


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no YAML frontmatter"
    end = text.find("\n---", 3)
    assert end > 0, f"{path.name} has an unterminated frontmatter block"
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if line.startswith(("  ", " ")) or not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_every_timestamped_document_declares_status() -> None:
    docs = timestamped_docs()
    assert len(docs) >= 40, f"expected the whole aiDoc history to be indexed, found {len(docs)}"
    for path in docs:
        fields = frontmatter(path)
        assert fields.get("doc_type") in DOC_TYPES, (
            f"{path.name}: doc_type={fields.get('doc_type')!r}"
        )
        assert fields.get("status") in STATUSES, f"{path.name}: status={fields.get('status')!r}"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields.get("as_of", "")), (
            f"{path.name}: as_of={fields.get('as_of')!r}"
        )


def test_superseded_documents_point_at_existing_documents() -> None:
    for path in timestamped_docs():
        text = path.read_text(encoding="utf-8")
        block = text[3 : text.find("\n---", 3)]
        if "superseded_by:" not in block:
            continue
        section = block.split("superseded_by:", 1)[1].split("\nnote:", 1)[0]
        targets = [line.strip("- ").strip() for line in section.splitlines() if line.strip()]
        assert targets, f"{path.name}: superseded_by declared but empty"
        for target in targets:
            # entries are aiDoc-relative; ../ reaches the repository root
            resolved = (AIDOC / target).resolve()
            assert resolved.exists(), f"{path.name}: superseded_by -> {target} missing"


def test_index_is_generated_from_frontmatter() -> None:
    assert INDEX_PATH.exists(), "missing aiDoc/20261115-1649-doc-status-index.md"
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
