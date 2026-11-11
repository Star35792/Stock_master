"""低价超跌反弹回测入口：先同步数据，再跑回测。

实现用户“回测时自动对时采集数据”的需求：
1. 调用 ``backend/scripts/scheduler/run_daily_loop.py`` 执行日级数据→因子闭环，
   把 market 数据补齐到最新交易日；
2. 如果同步成功，再调用 ``backend/research/backtests/low_price_rebound_backtest.py``
   运行低价超跌反弹策略回测；
3. 所有与回测相关的命令行参数都会透传给回测脚本。

用法示例::

    python backend/scripts/run_low_price_rebound_with_sync.py \
        --start 2024-10-14 --end 2026-10-20 --rebalance-days 20 --top-n 20 \
        --rank-mode inverse
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

# Prefer the project's own venv so that all pinned dependencies are available.
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

DAILY_LOOP_SCRIPT = BACKEND_DIR / "scripts" / "scheduler" / "run_daily_loop.py"
BACKTEST_SCRIPT = BACKEND_DIR / "research" / "backtests" / "low_price_rebound_backtest.py"


def _env_with_pythonpath() -> dict[str, str]:
    """确保 backend 根目录在 PYTHONPATH 中，否则 scripts 入口找不到 app 包。"""
    env = dict(os.environ)
    backend_path = str(BACKEND_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        env["PYTHONPATH"] = f"{backend_path}{os.pathsep}{existing}"
    else:
        env["PYTHONPATH"] = backend_path
    return env


def _run_daily_loop(sync_days: int, factor_lookback_days: int) -> int:
    """运行一次日级数据→因子闭环。"""
    cmd = [
        PYTHON_EXE,
        str(DAILY_LOOP_SCRIPT),
        "--sync-days",
        str(sync_days),
        "--factor-lookback-days",
        str(factor_lookback_days),
        "--sync-adjust-type",
        "none",
        "--factor-adjust-type",
        "qfq",
        "--refresh-reference-data",
        "--code-version",
        "local",
    ]
    logger.info("启动数据采集: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, cwd=BACKEND_DIR, check=False, text=True, env=_env_with_pythonpath()
    )
    return result.returncode


def _run_backtest(backtest_argv: list[str]) -> int:
    """透传参数运行低价超跌反弹回测。"""
    cmd = [PYTHON_EXE, str(BACKTEST_SCRIPT), *backtest_argv]
    logger.info("启动回测: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, cwd=BACKEND_DIR, check=False, text=True, env=_env_with_pythonpath()
    )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="低价超跌反弹回测：先同步数据，再跑回测。",
        add_help=False,
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="跳过 daily_loop 数据同步，直接跑回测（调试用）。",
    )
    parser.add_argument(
        "--sync-days",
        type=int,
        default=14,
        help="daily_loop 同步回溯天数。",
    )
    parser.add_argument(
        "--factor-lookback-days",
        type=int,
        default=14,
        help="daily_loop 因子计算回溯天数。",
    )
    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        help="显示本入口帮助；回测脚本的帮助请直接运行 low_price_rebound_backtest.py --help。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    known, backtest_argv = parser.parse_known_args(argv)

    if known.help:
        parser.print_help()
        print(
            "\n以下参数以及所有未被识别的参数都会透传给 low_price_rebound_backtest.py:\n"
            "  --start, --end, --rebalance-days, --top-n, --rank-mode, ..."
        )
        return 0

    if not BACKTEST_SCRIPT.exists():
        logger.error("回测脚本不存在: %s", BACKTEST_SCRIPT)
        return 1

    if not known.skip_sync:
        sync_code = _run_daily_loop(
            sync_days=known.sync_days,
            factor_lookback_days=known.factor_lookback_days,
        )
        if sync_code != 0:
            logger.error("数据同步失败，退出码 %s；中止回测。", sync_code)
            return sync_code
        logger.info("数据同步完成。")
    else:
        logger.info("跳过数据同步（--skip-sync）。")

    backtest_code = _run_backtest(backtest_argv)
    if backtest_code != 0:
        logger.error("回测失败，退出码 %s。", backtest_code)
    return backtest_code


if __name__ == "__main__":
    sys.exit(main())
