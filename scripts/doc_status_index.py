"""Generate and verify the aiDoc timestamped-document status index.

Status truth lives in each document's own YAML frontmatter; this script only
aggregates it into ``aiDoc/<index file>``. Run with ``--check`` to fail when the
index has drifted from the frontmatter or a timestamped document is missing the
required keys.

Standard library only, and must work from any current working directory.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AIDOC = REPO_ROOT / "aiDoc"
INDEX_NAME = "20261115-1649-doc-status-index.md"
INDEX_PATH = AIDOC / INDEX_NAME

DOC_TYPES = ("decision", "plan", "evidence", "analysis", "config", "index")
STATUSES = ("current", "snapshot", "partially_superseded", "withdrawn")
STATUS_LABEL = {
    "current": "现行有效",
    "snapshot": "一次性快照",
    "partially_superseded": "部分被取代",
    "withdrawn": "已撤销",
}

STAMPED = re.compile(r"^20\d{6}(-\d{4})?-.*\.md$")
KEY = re.compile(r"^([a-z_]+):(?:\s*(.*))?$")
LIST_ITEM = re.compile(r"^\s+-\s+(.*)$")


class DocEntry:
    def __init__(self, path: Path, meta: dict[str, object]) -> None:
        self.path = path
        self.meta = meta

    @property
    def rel(self) -> str:
        return self.path.relative_to(AIDOC).as_posix()

    @property
    def status(self) -> str:
        return str(self.meta["status"])

    def link(self, entry: str) -> str:
        """Render a ``superseded_by`` entry as a link valid from the index file.

        Entries are relative to ``aiDoc/``; ``../`` reaches the repository root.
        """
        target = (AIDOC / entry).resolve()
        if not target.exists():
            return f"`{entry}`"
        try:
            rel = target.relative_to(AIDOC).as_posix()
        except ValueError:
            rel = "../" + target.relative_to(REPO_ROOT).as_posix()
        return f"[`{rel}`]({rel})"


def parse_frontmatter(path: Path) -> dict[str, object] | str:
    """Return the frontmatter mapping, or an error string describing the defect."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return "missing YAML frontmatter"
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return "unterminated YAML frontmatter"

    meta: dict[str, object] = {}
    superseded: list[str] = []
    note: list[str] = []
    mode = ""
    for line in lines[1:end]:
        item = LIST_ITEM.match(line)
        if mode == "superseded_by" and item:
            superseded.append(item.group(1).strip())
            continue
        if mode == "note" and (line.startswith("  ") or not line.strip()):
            if line.strip():
                note.append(line.strip())
            continue
        matched = KEY.match(line)
        if not matched:
            return f"unparsable frontmatter line: {line!r}"
        key, value = matched.group(1), (matched.group(2) or "").strip()
        mode = key
        if key == "superseded_by":
            superseded = []
            meta[key] = superseded
        elif key == "note":
            note = []
            meta[key] = ""
        else:
            meta[key] = value
    if superseded:
        meta["superseded_by"] = superseded
    meta["note"] = " ".join(note)
    return meta


def validate(path: Path, meta: object) -> list[str]:
    if isinstance(meta, str):
        return [f"{path}: {meta}"]
    problems: list[str] = []
    for key in ("doc_type", "status", "as_of"):
        if not meta.get(key):
            problems.append(f"{path}: missing {key}")
    if meta.get("doc_type") not in DOC_TYPES:
        problems.append(f"{path}: doc_type={meta.get('doc_type')!r} not in {DOC_TYPES}")
    if meta.get("status") not in STATUSES:
        problems.append(f"{path}: status={meta.get('status')!r} not in {STATUSES}")
    as_of = str(meta.get("as_of", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        problems.append(f"{path}: as_of={as_of!r} is not YYYY-MM-DD")
    return problems


def collect() -> tuple[list[DocEntry], list[str]]:
    entries: list[DocEntry] = []
    problems: list[str] = []
    for path in sorted(AIDOC.rglob("*.md")):
        if path == INDEX_PATH or not STAMPED.match(path.name):
            continue
        meta = parse_frontmatter(path)
        problems += validate(path, meta)
        if isinstance(meta, dict):
            entries.append(DocEntry(path, meta))
    return entries, problems


def render(entries: list[DocEntry]) -> str:
    counts = {status: sum(1 for e in entries if e.status == status) for status in STATUSES}
    out = [
        "---",
        "doc_type: index",
        "status: current",
        f"as_of: {INDEX_NAME[:4]}-{INDEX_NAME[4:6]}-{INDEX_NAME[6:8]}",
        "note: >-",
        "  本表由各时间戳文档自身的 YAML frontmatter 生成（`uv run --project backend python scripts/doc_status_index.py`），状态真源是 frontmatter；索引不一致由 backend/tests/test_doc_status_index.py 挡住。",
        "---",
        "# 文档状态索引",
        "",
        "`aiDoc/` 下所有带时间戳前缀的文档都有一段 YAML frontmatter，标注它现在还能不能当现状用。本索引按状态聚合，供人和 AI 在引用历史文档前先查一次。",
        "",
        "## 状态词表",
        "",
        "- `current`（现行有效）：决策或口径仍然成立，可以作为当前依据引用。正文中的运行数字是当时快照，不因数字变化而降状态。",
        "- `snapshot`（一次性快照）：某次实验/回测/清单的结果记录，本身不声称仍然成立；引用时必须连同其日期与当时的偏差声明一起带出。",
        "- `partially_superseded`（部分被取代）：决策骨架仍在位，但正文里的某些事实已被更新的文档改写。只能引用未被取代的部分，取代去向见 `superseded_by`。",
        "- `withdrawn`（已撤销）：建议已被整体撤回，不得作为当前口径引用，仅保留作审计。",
        "",
        "字段约定：`doc_type` 取 " + " / ".join(f"`{item}`" for item in DOC_TYPES) + "；`as_of` 是该文档口径的最后核实日期，不是文件名里的生成时刻；`superseded_by` 列出取代它的文档，**路径相对 `aiDoc/`**（`../` 开头表示仓库根方向）；`note` 说明「哪一部分仍然作数」。",
        "",
        "## 双重标注",
        "",
        "一份文档可能同时「被当作现行依据引用」且「正文部分事实已过期」，这由 `status` 与 `note` 分别表达，不必为此新增第三种状态。例如 [研究库拆分决策](relations/20261024-1427-research-database-split.md) 的 `status: current` 表示拆分决策仍在执行，其 `note` 说明研究库表数已由 6 增为 7。",
        "",
        "## 引用规则",
        "",
        "- living docs（不匹配时间戳命名的 `roadmap.md`、`system-map.md`、`module-index.md`、`schema-plan.md`、`README.md` 等）承载当前状态，不带 frontmatter；时间戳文档只承载当时的决策与证据。",
        "- 两者冲突时以 living docs 与代码为准，并按 `aiDoc/README.md` 的同步规则回写时间戳文档的 `note` 或补一份新 ADR。",
        "- 本文件不复制各文档的结论，只做状态聚合；要读结论请打开原文。",
        "",
    ]
    for status in STATUSES:
        bucket = [e for e in entries if e.status == status]
        out.append(f"## {STATUS_LABEL[status]} `status: {status}`（{len(bucket)} 份）")
        out.append("")
        if not bucket:
            out += ["_（无）_", ""]
            continue
        out += ["| 文档 | 类型 | 截至 | 取代/去向 | 备注 |", "| --- | --- | --- | --- | --- |"]
        for entry in bucket:
            sup = entry.meta.get("superseded_by") or []
            sup_text = "、".join(entry.link(item) for item in sup) if sup else "—"
            note = (entry.meta.get("note") or "—").strip()
            out.append(
                f"| [`{entry.rel}`]({entry.rel}) | {entry.meta['doc_type']} "
                f"| {entry.meta['as_of']} | {sup_text} | {note} |"
            )
        out.append("")
    out.append(f"合计 {len(entries)} 份带时间戳文档。")
    out.append("")
    return "\n".join(out)


def command_check() -> int:
    entries, problems = collect()
    if problems:
        print("Invalid document frontmatter:", file=sys.stderr)
        for item in problems:
            print("  -", item, file=sys.stderr)
        return 1
    if not INDEX_PATH.exists():
        print(f"Missing status index file: {INDEX_PATH}", file=sys.stderr)
        return 1
    expected = render(entries)
    actual = INDEX_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    if actual != expected:
        print(
            f"Status index drifted from frontmatter ({len(entries)} documents: "
            + ", ".join(f"{s}={sum(1 for e in entries if e.status == s)}" for s in STATUSES)
            + "). Run: python scripts/doc_status_index.py",
            file=sys.stderr,
        )
        return 1
    counts = ", ".join(f"{s}={sum(1 for e in entries if e.status == s)}" for s in STATUSES)
    print(f"doc status index matches frontmatter: {len(entries)} documents, {counts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args(argv)
    entries, problems = collect()
    if problems:
        print("Invalid document frontmatter:", file=sys.stderr)
        for item in problems:
            print("  -", item, file=sys.stderr)
        return 1
    if args.check:
        return command_check()
    INDEX_PATH.write_text(render(entries), encoding="utf-8", newline="\n")
    counts = ", ".join(f"{s}={sum(1 for e in entries if e.status == s)}" for s in STATUSES)
    print(f"wrote {INDEX_PATH} docs={len(entries)} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
