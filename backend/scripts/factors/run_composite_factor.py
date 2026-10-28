from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal

from app.database.session import SessionLocal
from app.services import CompositeFactorService
from app.services.composite_factors import FACTOR_VERSION
from app.strategies import FactorScoreSpec


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def _parse_security_types(value: str | None) -> set[str] | None:
    if value is None:
        return None
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def _parse_components(value: str) -> list[FactorScoreSpec]:
    specs = []
    for item in value.split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) not in {2, 4} or not all(parts[:2]):
            raise ValueError(
                "components must use name:version or name:version:weight:direction"
            )
        weight = Decimal(parts[2]) if len(parts) == 4 else Decimal(1)
        higher_is_better = True
        if len(parts) == 4:
            if parts[3] not in {"high", "low"}:
                raise ValueError("component direction must be high or low")
            higher_is_better = parts[3] == "high"
        specs.append(
            FactorScoreSpec(
                factor_name=parts[0],
                factor_version=parts[1],
                weight=weight,
                higher_is_better=higher_is_better,
            )
        )
    return specs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate a weighted composite decision factor."
    )
    parser.add_argument("--start-date", required=True, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument(
        "--components",
        required=True,
        help=(
            "Comma-separated components: name:version or "
            "name:version:weight:direction, where direction is high or low."
        ),
    )
    parser.add_argument("--factor-name", default="decision_score")
    parser.add_argument("--factor-version", default=FACTOR_VERSION)
    parser.add_argument("--symbols", help="Optional comma-separated symbols.")
    parser.add_argument(
        "--security-types",
        help="Optional comma-separated security types, default stock,etf,index.",
    )
    parser.add_argument(
        "--code-version",
        default="local",
        help="Code version recorded in factor runs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        try:
            result = CompositeFactorService().run(
                db,
                factor_specs=_parse_components(args.components),
                start_date=date.fromisoformat(args.start_date),
                end_date=date.fromisoformat(args.end_date),
                factor_name=args.factor_name,
                factor_version=args.factor_version,
                symbols=_parse_symbols(args.symbols),
                allowed_security_types=_parse_security_types(args.security_types),
                code_version=args.code_version,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"factor.name={result.factor_name}")
    print(f"factor.version={result.factor_version}")
    print(f"factor.input_rows={result.input_rows}")
    print(f"factor.rows={result.rows_written}")
    print(f"factor.run_id={result.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
