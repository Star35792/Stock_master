"""Run from backend: python -m scripts.research.screen_quality_value_attention INPUT.json"""

import argparse
import json
import sys
from pathlib import Path

from app.strategies.quality_value_attention import ScreenRequest, screen


def main() -> None:
    parser = argparse.ArgumentParser(description="双低一高：只读证据选股，不生成交易订单")
    parser.add_argument("input", type=Path, help="Curated market/factor evidence JSON")
    args = parser.parse_args()
    request = ScreenRequest.model_validate_json(args.input.read_text(encoding="utf-8"))
    # Windows redirected stdout otherwise uses the locale code page (e.g. GBK).
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(screen(request), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
