from __future__ import annotations

from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "research" / "backtests" / "render_report.py"
)


def test_report_script_targets_backend_outputs_and_has_no_stale_metrics() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'OUT = BACKEND_DIR / "outputs" / "backtest"' in source
    assert 'DOC = ROOT / "aiDoc" / "data"' in source
    assert 'STAMP = "2024-10-14_2026-10-14_20d_top20_intent"' in source
    assert "0.2475" not in source
    assert "0.1220" not in source
    assert "0.6328" not in source
    assert "未找到与当前数据快照绑定的参数敏感性产物" in source
    assert "拒绝展示旧的硬编码比较结果" in source
    assert "INTENT_MANIFEST" in source
    assert "锁定四件套路径、大小与 SHA-256" in source
