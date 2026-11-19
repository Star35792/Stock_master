"""Bounded reruns of a single recorded factor, without changing its definition."""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from importlib import import_module
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.lake.curated_reader import CuratedDailyBarReader
from app.repositories.factor import FactorDefinitionRepository, FactorRunRepository
from app.repositories.factor.recovery import FactorRecoveryRepository
from app.repositories.lake import ReleaseRepository
from app.repositories.warm_layer import WarmLayerStore
from app.services.release import ReleaseService

MAX_INPUT_ROWS = 2_000_000
MAX_SYMBOLS = 8_000
FAMILIES = (
    ("basic_price_volume", "BasicPriceVolumeFactorService", "BASIC_FACTOR_SPECS"),
    ("financial", "FinancialFactorService", "FINANCIAL_FACTOR_SPECS"),
    ("valuation", "ValuationFactorService", "VALUATION_FACTOR_SPECS"),
    ("industry", "IndustryFactorService", "INDUSTRY_FACTOR_SPECS"),
    ("sentiment", "SentimentFactorService", "SENTIMENT_FACTOR_SPECS"),
    ("macro", "MacroFactorService", "MACRO_FACTOR_SPECS"),
    ("policy_event", "PolicyEventFactorService", "POLICY_EVENT_FACTOR_SPECS"),
    ("earnings_revision", "EarningsRevisionFactorService", "EARNINGS_REVISION_FACTOR_SPECS"),
    ("capital_flow", "CapitalFlowFactorService", "CAPITAL_FLOW_FACTOR_SPECS"),
    ("crowding", "CrowdingFactorService", None),
)


@dataclass
class RecoveryContext:
    original: Any
    module: Any
    factory: Any
    spec: Any
    family: str
    start_date: date
    end_date: date
    input_start_date: date
    symbols: list[str] | None
    adjust_type: str


class RecordedPriceReader:
    """Hash the actual ordered prices consumed by the existing calculator."""

    def __init__(self, reader) -> None:
        self.reader = reader
        self.digest = sha256()

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def list_for_factor_calculation(self, db, **parameters):
        rows = self.reader.list_for_factor_calculation(db, **parameters)
        if len(rows) > MAX_INPUT_ROWS:
            raise ValueError("实际价格输入超出200万行上限")
        if len({row.symbol for row in rows}) > MAX_SYMBOLS:
            raise ValueError("实际价格输入证券数超出8000只上限")
        fields = (
            "symbol",
            "trade_date",
            "security_type",
            "adjust_type",
            "close",
            "volume",
            "amount",
            "turnover_rate",
            "selected_raw_id",
            "source_name",
        )
        for row in rows:
            self.digest.update(
                json.dumps(
                    [getattr(row, field, None) for field in fields],
                    ensure_ascii=True,
                    default=str,
                    separators=(",", ":"),
                ).encode()
            )
            self.digest.update(b"\n")
        return rows


class FactorRecoveryService:
    def __init__(self, repository: FactorRecoveryRepository | None = None) -> None:
        self.repository = repository or FactorRecoveryRepository()
        self.runs = FactorRunRepository()
        self.definitions = FactorDefinitionRepository()

    def resolve(self, db: Session, original_run_id: int) -> RecoveryContext:
        original = self.runs.get_by_id(db, original_run_id)
        if original is None:
            raise ValueError("原因子运行不存在")
        if original.status not in {"failed", "partial"} and not (
            original.status == "success" and original.rows_written == 0
        ):
            raise ValueError("仅支持失败、部分完成或零观察的因子运行")
        definition = self.definitions.get_by_name_version(
            db,
            name=original.factor_name,
            version=original.factor_version,
        )
        if definition is None or not definition.is_active or definition.status == "retired":
            raise ValueError("因子定义不存在、已停用或已退役，不能自动重算")
        parameters = original.parameters or {}
        required = {"target_start_date", "target_end_date", "symbols", "adjust_type"}
        if not required.issubset(parameters) or parameters.get("quality_status") != "trusted":
            raise ValueError("原运行缺少可信输入口径或完整目标窗口，需人工核对")
        start = date.fromisoformat(parameters["target_start_date"])
        end = date.fromisoformat(parameters["target_end_date"])
        if start > end or (end - start).days > 366:
            raise ValueError("重算目标窗口必须有序，且不超过367个自然日")
        adjust_type = parameters["adjust_type"]
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("原复权口径无法恢复")
        symbols = parameters["symbols"]
        if symbols is not None:
            if not isinstance(symbols, list) or not symbols or len(symbols) > MAX_SYMBOLS:
                raise ValueError("原证券范围缺失或超出8000只上限")
            if any(not isinstance(item, str) or not item.strip() for item in symbols):
                raise ValueError("原证券范围含非法代码")
            symbols = sorted({item.strip().upper() for item in symbols})
        for family, class_name, specs_name in FAMILIES:
            module = import_module(f"app.services.{family}_factors")
            specs = getattr(module, specs_name) if specs_name else ()
            spec = next((s for s in specs if s.name == original.factor_name), None)
            if spec is None and not (
                family == "crowding" and original.factor_name == module.FACTOR_NAME
            ):
                continue
            version = module.FACTOR_VERSION
            if family in {"basic_price_volume", "crowding"}:
                version = f"{version}-{adjust_type}"
            if version != original.factor_version:
                raise ValueError("原因子版本不是当前计算器支持的版本，不能自动替换")
            lookback = (
                spec.window_days * 3
                if family == "basic_price_volume"
                else getattr(module, "LOOKBACK_DAYS", 0)
            )
            return RecoveryContext(
                original,
                module,
                getattr(module, class_name),
                spec,
                family,
                start,
                end,
                start - timedelta(days=lookback),
                symbols,
                adjust_type,
            )
        raise ValueError("该因子不在可安全恢复的计算器白名单内")

    def _reader(self, db: Session, context: RecoveryContext):
        if context.adjust_type == "qfq":
            release = ReleaseRepository().get_active(db, dataset="curated.prices_qfq")
            if release is None or release.quality_status != "trusted":
                raise ValueError("缺少已激活且可信的前复权价格发布，请先完成日级数据闭环")
            root = WarmLayerStore().prices_qfq_glob()
            if (
                not root.exists()
                or ReleaseService.compute_parquet_checksum(root) != release.checksum
            ):
                raise ValueError("前复权价格文件与激活发布不一致，请先核对数据发布")
            return CuratedDailyBarReader(dataset_path=root), release
        return self.repository.control_reader(), None

    def inspect(self, db: Session, original_run_id: int) -> tuple[RecoveryContext, dict]:
        context = self.resolve(db, original_run_id)
        checks = {
            "factor_name": context.original.factor_name,
            "factor_version": context.original.factor_version,
            "target_start_date": context.start_date.isoformat(),
            "target_end_date": context.end_date.isoformat(),
            "adjust_type": context.adjust_type,
            "symbols_count": len(context.symbols) if context.symbols else "all",
            "original_code_version": context.original.code_version,
            "current_code_version": settings.app_code_version,
            "original_release_id": context.original.release_id,
            "source_release_id": None,
            "reason_codes": [],
            "input_rows": 0,
            "next_action": None,
        }
        try:
            reader, release = self._reader(db, context)
        except (ValueError, LookupError, OSError) as exc:
            checks["reason_codes"] = ["PRICE_RELEASE_UNAVAILABLE"]
            checks["detail"] = str(exc)
            checks["next_action"] = "manual_review"
            return context, checks
        counts = self.repository.price_counts(
            db,
            reader=reader,
            start_date=context.input_start_date,
            end_date=context.end_date,
            adjust_type=context.adjust_type,
            symbols=context.symbols,
            security_types=tuple(context.module.SUPPORTED_SECURITY_TYPES),
        )
        checks["source_release_id"] = release.release_id if release else None
        checks["input_rows"] = sum(counts.values())
        checks["target_rows"] = sum(n for day, n in counts.items() if day >= context.start_date)
        target_dates = self.repository.target_dates(
            db,
            start_date=context.start_date,
            end_date=context.end_date,
            symbols=context.symbols,
        )
        checks["target_trade_dates"] = len(target_dates)
        reasons = checks["reason_codes"]
        if not target_dates:
            reasons.append("TARGET_TRADING_CALENDAR_MISSING")
        missing_dates = sorted(target_dates - set(counts))
        if missing_dates:
            reasons.append("TRUSTED_PRICE_DATES_MISSING")
            checks["missing_price_dates"] = [day.isoformat() for day in missing_dates[:20]]
        if not checks["input_rows"] or not checks["target_rows"]:
            reasons.append("TRUSTED_PRICE_INPUT_MISSING")
            checks["next_action"] = "daily_data_sync"
        if checks["input_rows"] > MAX_INPUT_ROWS:
            reasons.append("INPUT_ROW_LIMIT_EXCEEDED")
        if release is not None and (
            release.max_date is None or (target_dates and release.max_date < max(target_dates))
        ):
            reasons.append("PRICE_RELEASE_DATE_INCOMPLETE")
        if context.family == "basic_price_volume":
            if len(counts) <= context.spec.window_days:
                reasons.append("PRICE_WARMUP_INSUFFICIENT")
        per_symbol = self.repository.symbol_price_counts(
            db,
            reader=reader,
            start_date=context.input_start_date,
            target_start_date=context.start_date,
            end_date=context.end_date,
            adjust_type=context.adjust_type,
            symbols=context.symbols,
            security_types=tuple(context.module.SUPPORTED_SECURITY_TYPES),
        )
        if len(per_symbol) > MAX_SYMBOLS:
            reasons.append("SYMBOL_LIMIT_EXCEEDED")
        applicable = set(context.symbols or [])
        if context.symbols:
            applicable, unknown = self.repository.applicable_symbols(
                db, symbols=context.symbols,
                security_types=tuple(context.module.SUPPORTED_SECURITY_TYPES),
            )
            applicable |= unknown
            checks["applicable_symbols_count"] = len(applicable)
            checks["inapplicable_symbols_count"] = len(context.symbols) - len(applicable)
        missing_symbols = [
            symbol
            for symbol in sorted(applicable)
            if symbol not in per_symbol or not per_symbol[symbol][1]
        ]
        if missing_symbols:
            reasons.append("REQUESTED_SYMBOL_PRICE_MISSING")
            checks["missing_price_symbols"] = missing_symbols[:20]
        if context.family == "basic_price_volume" and context.symbols:
            insufficient = [
                symbol
                for symbol in sorted(applicable)
                if (per_symbol.get(symbol, (0, 0))[0] <= context.spec.window_days)
            ]
            if insufficient:
                if "PRICE_WARMUP_INSUFFICIENT" not in reasons:
                    reasons.append("PRICE_WARMUP_INSUFFICIENT")
                checks["insufficient_history_symbols"] = insufficient[:20]
        module, spec = context.module, context.spec
        datasets = tuple(getattr(spec, "datasets", ()))
        if not datasets and context.family in {"macro", "policy_event"}:
            datasets = (module.DATASET,)
        components = ()
        if context.family == "crowding":
            version = f"{module.COMPONENT_FACTOR_VERSION}-{context.adjust_type}"
            components = (
                (module.TURNOVER_FACTOR_NAME, version),
                (module.ABNORMAL_VOLUME_FACTOR_NAME, version),
                (module.SENTIMENT_FACTOR_NAME, module.COMPONENT_FACTOR_VERSION),
            )
        dependencies = self.repository.dependency_counts(
            db,
            start_date=context.input_start_date,
            end_date=context.end_date,
            symbols=context.symbols,
            financial=context.family == "financial",
            valuation=context.family == "valuation",
            datasets=datasets,
            components=components,
        )
        checks["dependencies"] = dependencies
        checks["missing_dependencies"] = [key for key, count in dependencies.items() if not count]
        if checks["missing_dependencies"]:
            reasons.append("UPSTREAM_INPUT_MISSING")
            if datasets and set(datasets).issubset(
                {
                    "hot_stock",
                    "skyrocket",
                    "limit_up_pool",
                    "limit_down_pool",
                    "failed_limit_up",
                    "limit_up_ladder",
                    "dragon_tiger",
                    "eastmoney_main_fund_rank",
                }
            ):
                checks["next_action"] = "alternative_data_sync"
        return context, checks

    def execute(self, db: Session, *, original_run_id: int, action_id: int) -> tuple[dict, str]:
        context, checks = self.inspect(db, original_run_id)
        if checks["reason_codes"]:
            raise ValueError(f"输入复查未通过：{checks}")
        reader, release = self._reader(db, context)
        recorded = RecordedPriceReader(reader)
        result = context.factory(daily_bar_repository=recorded).run(
            db,
            start_date=context.start_date,
            end_date=context.end_date,
            symbols=context.symbols,
            adjust_type=context.adjust_type,
            code_version=settings.app_code_version,
            release_id=release.release_id if release else None,
            **(
                {"factor_names": [context.original.factor_name]}
                if context.family != "crowding"
                else {}
            ),
        )
        if release is not None:
            current = ReleaseRepository().get_active(db, dataset="curated.prices_qfq")
            if (
                current is None
                or current.release_id != release.release_id
                or ReleaseService.compute_parquet_checksum(WarmLayerStore().prices_qfq_glob())
                != release.checksum
            ):
                raise ValueError("重算期间前复权输入发布发生变化，本次结果不提交")
        run_ids = getattr(result, "run_ids", None)
        if run_ids is None:
            run_ids = {context.original.factor_name: result.run_id}
        new_id = run_ids[context.original.factor_name]
        new_run = self.runs.get_by_id(db, new_id)
        if new_run is None or new_run.factor_version != context.original.factor_version:
            raise ValueError("重算结果版本或运行引用不一致")
        identity = {
            "original_run_id": original_run_id,
            "operator_action_id": action_id,
            "original_code_version": context.original.code_version,
            "original_release_id": context.original.release_id,
            "actual_code_version": settings.app_code_version,
            "actual_price_release_id": release.release_id if release else None,
            "actual_price_checksum": release.checksum if release else None,
            "actual_price_input_sha256": recorded.digest.hexdigest(),
        }
        summary = self.repository.output_summary(db, run_id=new_id)
        summary["expected_price_observations"] = checks["target_rows"]
        summary["expected_trade_dates"] = checks["target_trade_dates"]
        expected_symbols = self.repository.symbol_price_counts(
            db,
            reader=reader,
            start_date=context.input_start_date,
            target_start_date=context.start_date,
            end_date=context.end_date,
            adjust_type=context.adjust_type,
            symbols=context.symbols,
            security_types=tuple(context.module.SUPPORTED_SECURITY_TYPES),
        )
        output_symbols = self.repository.output_symbol_counts(db, run_id=new_id)
        incomplete_symbols = [
            symbol
            for symbol, (_total, target) in expected_symbols.items()
            if target and output_symbols.get(symbol, 0) != target
        ]
        summary["incomplete_symbol_count"] = len(incomplete_symbols)
        summary["incomplete_symbol_examples"] = incomplete_symbols[:20]
        # Empty output remains an observation gap, never a repaired problem.
        closed = (
            summary["rows"] > 0
            and new_run.status == "success"
            and summary["rows"] == checks["target_rows"]
            and summary["trade_dates"] == checks["target_trade_dates"]
            and not incomplete_symbols
        )
        identity["request_identity"] = sha256(
            repr((original_run_id, context.start_date, context.end_date, context.symbols)).encode()
        ).hexdigest()
        new_run.metadata_ = {**(new_run.metadata_ or {}), "operator_recovery": identity}
        return {
            "original_run_id": original_run_id,
            "new_run_id": new_id,
            "problem_closed": closed,
            "availability": "available" if closed else "partial" if summary["rows"] else "empty",
            "recheck": summary,
            "provenance": identity,
        }, "success" if closed else "partial"
