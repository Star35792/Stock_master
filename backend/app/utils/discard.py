"""Unpublish throw-away directories before reclaiming their disk space.

A directory is moved out of the read path first. The current and older trash
trees are then removed; failed removals stay isolated for the next pass.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

#: Directory that isolates unpublished trees during removal.
TRASH_DIRNAME = ".trash"
logger = logging.getLogger(__name__)


def _stamp() -> str:
    """Return a sortable stamp for the trash directory name."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def discard_directory(path: Path, *, trash_root: Path | None = None) -> Path | None:
    """Unpublish and remove ``path``; return its trash path only if removal fails."""
    if not path.exists():
        return None
    root = trash_root or (path.parent / TRASH_DIRNAME)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{path.name}.trash-{_stamp()}"
    counter = 1
    while target.exists():
        target = root / f"{path.name}.trash-{_stamp()}-{counter}"
        counter += 1
    shutil.move(str(path), str(target))
    for discarded in root.iterdir():
        if not discarded.is_dir() or ".trash-" not in discarded.name:
            continue
        try:
            shutil.rmtree(discarded)
        except OSError:
            logger.warning("Could not remove discarded directory: %s", discarded, exc_info=True)
    return target if target.exists() else None


__all__ = ["TRASH_DIRNAME", "discard_directory"]
