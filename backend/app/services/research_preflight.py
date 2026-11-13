"""Read-only preview of the existing factor-selection execution path."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.backtest import DailyEventBacktestEngine, TargetWeightSignal
from app.backtest.fees import build_fee_model
from app.backtest.rebalance import select_rebalance_dates
from app.portfolio import PortfolioConstraints
from app.schemas.strategy import FactorSelectionBacktestRequest
from app.strategies import FactorScoreSpec, MultiFactorStrategy, StrategyFactorFrame


def check_factor_selection(
    db: Session,
    *,
    payload: FactorSelectionBacktestRequest,
    service,
    portfolio_constraints: PortfolioConstraints | None,
) -> dict:
    """Preview the same factor reader, strategy and daily engine without persisting a run.

    This is a point-in-time diagnostic. The real run re-reads and rechecks every
    input; the preview does not certify data provenance or investment results.
    """
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    report = {
        "rule_version": "factor_selection_preflight_v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "input_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "runnable_now": False,
        "evidence_qualified": False,
        "checks": [],
    }

    def add(code: str, status: str, detail: str, next_action: str = "") -> None:
        report["checks"].append({
            "code": code, "status": status, "detail": detail,
            "next_action": next_action,
        })

    if payload.start_date > payload.end_date:
        add("window", "blocked", "开始日期晚于结束日期", "调整回测区间")
        return report
    if payload.validation_mode != "none":
        add("validation", "blocked", "此预检当前只支持单次探索性回测", "改用单次回测或专用滚动入口")
        return report
    if payload.quantile_backtest is not None:
        add("quantile", "blocked", "此预检尚不覆盖分层回测附加结果", "关闭分层附加结果后预检")
        return report
    if payload.signal_lag != 1 or payload.holding_period != 1:
        add("strategy_contract", "blocked", "只支持次日执行和一期持有", "核对回测参数")
        return report

    specs = [FactorScoreSpec(
        factor_name=item.factor_name, factor_version=item.factor_version,
        weight=item.weight, higher_is_better=item.higher_is_better,
    ) for item in payload.factor_specs]
    try:
        normalized = service._normalize_factor_specs(specs)
        rows = service._load_factor_rows(db, normalized, payload.start_date, payload.end_date)
        symbols = {row.symbol for group in rows.values() for row in group}
        types = service.security_repository.get_security_types(db, symbols)
        normalized_types = {symbol.upper(): kind for symbol, kind in types.items()}
        frame = StrategyFactorFrame.from_rows(as_of_date=payload.end_date, rows_by_factor=rows)
        add(
            "factor", "passed",
            f"读取 {len(normalized)} 个因子、{len(frame.observation_dates)} 个观察日",
        )

        decision_dates = None
        if payload.rebalance_rule != "daily":
            open_dates = service.trading_calendar_repository.list_open_dates(
                db, exchange="SH", start_date=payload.start_date, end_date=payload.end_date,
            )
            selected_dates = select_rebalance_dates(
                open_dates, rule=payload.rebalance_rule, interval=payload.rebalance_interval,
            )
            decision_dates = selected_dates & set(frame.observation_dates)
            if not decision_dates:
                raise ValueError(
                    "rebalance rule leaves no decision date with factor "
                    "observations in the requested window"
                )
        add("calendar", "passed", "调仓日期与因子观察日有交集")

        signals = MultiFactorStrategy(
            top_n=payload.top_n,
            allowed_security_types=set(payload.allowed_security_types),
            coverage_ratio=payload.coverage_ratio,
            weighting_mode=payload.weighting_mode,
            minimum_score_policy=payload.minimum_score_policy,
            minimum_score=payload.minimum_score,
            invested_weight=payload.invested_weight,
        ).generate_history(
            frame=frame, security_types=normalized_types,
            decision_dates=sorted(decision_dates) if decision_dates else None,
        )
        if not signals:
            raise ValueError("no eligible strategy signals in the requested window")
        normalized_signals = [
            TargetWeightSignal(signal.signal_date, signal.symbol.upper(), signal.target_weight)
            for signal in signals
        ]
        signal_symbols = {signal.symbol for signal in normalized_signals}
        add("signals", "passed", f"生成 {len(signals)} 条模拟信号")

        fee = build_fee_model(
            fee_model_version=payload.fee_model_version,
            commission_rate=payload.commission_rate,
            slippage_bps=payload.slippage_bps,
            min_commission=payload.min_commission,
            sell_stamp_duty_rate=payload.sell_stamp_duty_rate,
            transfer_fee_rate=payload.transfer_fee_rate,
            exchange_fee_rate=payload.exchange_fee_rate,
        )
        with service.backtest_service._research_read(
            db, start_date=payload.start_date, end_date=payload.end_date,
        ) as (reader, scope):
            actions = reader.list_corporate_actions(
                symbols=signal_symbols, start_date=payload.start_date, end_date=payload.end_date,
            )
            if actions:
                raise ValueError(
                    "backtest interval contains corporate actions; "
                    "event accounting is not implemented"
                )
            bars = reader.list_daily_bars(
                start_date=payload.start_date, end_date=payload.end_date,
                adjust_type="none", symbols=signal_symbols, quality_status="trusted",
            )
            calendar = reader.load_execution_calendar(
                symbols=signal_symbols, start_date=payload.start_date, end_date=payload.end_date,
            )
            profiles = {}
            if portfolio_constraints is not None:
                profiles = {
                    security.symbol.upper(): (
                        security.security_type, security.industry or "unknown"
                    )
                    for security in service.security_repository.get_many(db, symbols=signal_symbols)
                }
            preview = DailyEventBacktestEngine(
                initial_cash=payload.initial_cash, lot_size=payload.lot_size,
                fee_model=fee, portfolio_constraints=portfolio_constraints,
                security_profiles=profiles,
            ).run(bars=bars, signals=normalized_signals, execution_calendar=calendar)
            add(
                "execution", "passed",
                f"{scope}：可信未复权行情可运行，预演成交 {len(preview.trades)} 笔",
            )
            if payload.benchmark:
                benchmark_bars = reader.list_daily_bars(
                    start_date=payload.start_date, end_date=payload.end_date,
                    adjust_type=payload.adjust_type,
                    symbols={payload.benchmark.upper()}, quality_status="trusted",
                )
                benchmark_dates = [bar.trade_date for bar in benchmark_bars]
                equity_dates = [point.trade_date for point in preview.equity_curve]
                if len(equity_dates) < 2 or benchmark_dates != equity_dates:
                    add(
                        "benchmark", "caveat", "基准与策略净值日期未完整对齐，运行后不可比较",
                        "补齐可信基准行情",
                    )
                else:
                    add("benchmark", "passed", "基准与策略净值日期对齐")
            else:
                add("benchmark", "caveat", "未配置基准，不能计算超额收益", "按需选择可信基准")
        report["runnable_now"] = True
    except (ValueError, RuntimeError) as exc:
        add("run_blocker", "blocked", str(exc), "核对因子、可信行情、交易日历及公司行为")

    add(
        "evidence", "caveat",
        "历史 ST、行业、退市、公司行为及因子首次可得时间的证据仍需逐项认证；预检不授予收益资格",
        "查看研究合同与数据血缘",
    )
    return report
