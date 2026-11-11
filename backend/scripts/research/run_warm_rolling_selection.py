"""通过平台研究链路在温层上跑 rolling walk-forward 因子选股回测。

复用 ``run_warm_factor_selection`` 的因子预设与费用合同；逐窗执行
``run_rolling_factor_selection``（训练窗产物快照绑定 artifact，测试窗
out-of-sample 执行），汇总逐窗净收益与绩效的稳定性。

``--selection-policy training_ic_v1`` 启用训练窗自动选参：每个训练窗内
按预注册阈值（|RankIC1|>=0.02、|IR|>=0.30、方向正率>=0.55、观测>=10）
对候选因子计算日历对齐 RankIC1 证据，方向相对请求预设只「维持或拒绝」
（<60 开市日的窗不允许翻转），存活因子等权进入测试窗；逐窗选择证据写
入 ``training_artifact``（快照 v2）。默认 ``none`` 保持历史行为。

已知限制（与单窗口脚本一致，另加）
----------------------------------
- 默认（none）训练窗不做参数选择：``training_artifact`` 只是训练期因子
  快照，因子方向与权重沿用请求预设（``ic_aligned`` 的方向来自全样本
  IC 证据，存在前视嫌疑，逐窗结论只能验证「同一规则跨期稳定性」）。
- 启用 training_ic_v1 时新增限制：方向来自短训练窗证据（样本量小）、
  阈值为人为预注册而非统计估计、被拒窗口会中止整次运行从而改变被评估
  的窗口集合；选择器前瞻收益口径为 curated prices_qfq + 交易日历对齐，
  与 factor_profile 的 LEAD 口径不可直接比较。
- ``every_n_trading_days`` 锚定各测试窗首个开市日，窗口间相位不一致。
- 其余偏差（公司行为、幸存者、行业时点、停牌、单样本段）同
  ``run_warm_factor_selection`` 头部声明，全部成立。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("RESEARCH_READ_SOURCE", "warm")

_PRE_PARSER = argparse.ArgumentParser(add_help=False)
_PRE_PARSER.add_argument("--release-tag", default="20261022-181648")
_PRE_ARGS, _ = _PRE_PARSER.parse_known_args()
os.environ.setdefault("RESEARCH_RELEASE_TAG", _PRE_ARGS.release_tag)

from run_warm_factor_selection import (  # noqa: E402
    FACTOR_LIMITATIONS,
    FACTOR_VERSION,
    SPEC_SETS,
)
from sqlalchemy.orm import Session  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.database.research_session import (  # noqa: E402
    ResearchSessionLocal,
    initialize_research_database,
)
from app.database.session import SessionLocal  # noqa: E402
from app.portfolio import PortfolioConstraints  # noqa: E402
from app.schemas.strategy import BacktestPortfolioConstraintsRequest  # noqa: E402
from app.services.performance import BacktestPerformanceService  # noqa: E402
from app.services.strategy_research import StrategyResearchService  # noqa: E402
from app.services.training_factor_selector import (  # noqa: E402
    build_training_selection_policy,
)
from app.strategies import FactorScoreSpec, WeightingMode  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="温层 rolling walk-forward 因子选股回测")
    parser.add_argument("--start-date", default="2025-11-03")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--factor-version",
        default=FACTOR_VERSION,
        help="候选因子版本；ic2y release 的两年回填用 v2-qfq-2y",
    )
    parser.add_argument("--spec-set", choices=sorted(SPEC_SETS), default="ic_aligned")
    parser.add_argument("--release-tag", default="20261022-181648")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--invested-weight", default="1")
    parser.add_argument("--exchange", default="SH")
    parser.add_argument("--training-days", type=int, default=40)
    parser.add_argument("--test-days", type=int, default=40)
    parser.add_argument("--step-days", type=int, default=40)
    parser.add_argument(
        "--rebalance-rule",
        choices=["daily", "weekly", "monthly", "every_n_trading_days"],
        default="every_n_trading_days",
    )
    parser.add_argument("--rebalance-interval", type=int, default=20)
    parser.add_argument("--enable-portfolio-constraints", action="store_true")
    parser.add_argument("--max-symbol-weight", default="0.20")
    parser.add_argument("--max-industry-weight", default="0.40")
    parser.add_argument("--cash-reserve-rate", default="0.05")
    parser.add_argument(
        "--selection-policy",
        choices=["none", "training_ic_v1"],
        default="none",
        help="训练窗自动选参策略；training_ic_v1 按预注册阈值逐窗选因子与方向",
    )
    parser.add_argument(
        "--selection-min-survivors",
        type=int,
        default=None,
        help="覆盖 training_ic_v1 的最小存活因子数（默认 3；事后调整需在研究文档披露）",
    )
    parser.add_argument("--name", default=f"warm-rolling-{datetime.now():%Y%m%d-%H%M%S}")
    parser.add_argument("--code-version", default="local")
    parser.add_argument("--data-version", default="warm-20261022-175052")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    print(f"research_read_source = {settings.research_read_source}")
    print(
        f"窗口 {args.start_date} ~ {args.end_date}, exchange={args.exchange}, "
        f"train/test/step = {args.training_days}/{args.test_days}/{args.step_days}, "
        f"rebalance = {args.rebalance_rule}"
        + (
            f"(interval={args.rebalance_interval})"
            if args.rebalance_rule == "every_n_trading_days"
            else ""
        )
    )
    # The interval only carries meaning for every_n_trading_days; the service
    # correctly rejects other rules with interval != 1.
    args.rebalance_interval = (
        args.rebalance_interval if args.rebalance_rule == "every_n_trading_days" else 1
    )

    initialize_research_database()
    # 研究产物写独立 research.duckdb（拿研究写锁），行情/因子仍读主库与温层；
    # 两库分文件后，本脚本与 API/维护进程不再竞争同一把文件级写锁。
    with duckdb_write_lock("data/research_write.lock", timeout_seconds=120):
        research_db: Session = ResearchSessionLocal()
        try:
            return _run(args, research_db)
        finally:
            research_db.close()


def _run(args: argparse.Namespace, research_db: Session) -> int:
    db: Session = SessionLocal()
    try:
        specs = [
            FactorScoreSpec(
                factor_name=name,
                factor_version=args.factor_version,
                weight=weight,
                higher_is_better=higher,
            )
            for name, weight, higher in SPEC_SETS[args.spec_set]
        ]
        constraints_request = (
            BacktestPortfolioConstraintsRequest(
                max_symbol_weight=Decimal(args.max_symbol_weight),
                max_industry_weight=Decimal(args.max_industry_weight),
                cash_reserve_rate=Decimal(args.cash_reserve_rate),
            )
            if args.enable_portfolio_constraints
            else None
        )
        portfolio_constraints = (
            None
            if constraints_request is None
            else PortfolioConstraints(
                max_symbol_weight=constraints_request.max_symbol_weight,
                max_industry_weight=constraints_request.max_industry_weight,
                cash_reserve_rate=constraints_request.cash_reserve_rate,
                security_type_budgets=constraints_request.security_type_budgets,
            )
        )
        results = StrategyResearchService().run_rolling_factor_selection(
            db,
            exchange=args.exchange,
            start_date=date.fromisoformat(args.start_date),
            end_date=date.fromisoformat(args.end_date),
            training_days=args.training_days,
            test_days=args.test_days,
            step_days=args.step_days,
            name=args.name,
            factor_specs=specs,
            top_n=args.top_n,
            weighting_mode=(
                WeightingMode.EQUAL_WEIGHT_PARTIAL
                if Decimal(args.invested_weight) < 1
                else WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
            ),
            invested_weight=Decimal(args.invested_weight),
            rebalance_rule=args.rebalance_rule,
            rebalance_interval=args.rebalance_interval,
            portfolio_constraints=portfolio_constraints,
            allowed_security_types={"stock"},
            adjust_type="qfq",
            initial_cash=Decimal(args.initial_cash),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            fee_model_version="a_share_cost_v1",
            min_commission=Decimal("5"),
            sell_stamp_duty_rate=Decimal("0.0005"),
            transfer_fee_rate=Decimal("0.00001"),
            exchange_fee_rate=Decimal("0.0000487"),
            research_db=research_db,
            code_version=args.code_version,
            data_version=args.data_version,
            selection_policy=(
                build_training_selection_policy(
                    args.selection_policy,
                    min_surviving_factors=args.selection_min_survivors,
                )
                if args.selection_policy != "none"
                else None
            ),
        )
        research_db.commit()

        performance_service = BacktestPerformanceService()
        windows_payload = []
        returns: list[Decimal] = []
        sharpes: list[Decimal | None] = []
        drawdowns: list[Decimal] = []
        for window_result in results.windows:
            window = window_result.window
            persisted = window_result.persisted
            report = performance_service.analyze(
                db,
                research_db=research_db,
                run_id=persisted.run_id,
                code_version=args.code_version,
                style_factors=None,
            ).report
            returns.append(report.total_return)
            sharpes.append(report.sharpe_ratio)
            drawdowns.append(report.maximum_drawdown)
            windows_payload.append(
                {
                    "window_id": window.window_id,
                    "test_window": [
                        window.test_start_date.isoformat(),
                        window.test_end_date.isoformat(),
                    ],
                    "run_id": persisted.run_id,
                    "total_return": str(report.total_return),
                    "maximum_drawdown": str(report.maximum_drawdown),
                    "turnover_rate": str(report.turnover_rate),
                    "total_commission": str(report.total_commission),
                    "sharpe_ratio": (
                        str(report.sharpe_ratio) if report.sharpe_ratio is not None else None
                    ),
                    "selection": window_result.selection_summary,
                }
            )
            if window_result.selection_summary is not None:
                selected = ", ".join(
                    f"{item['factor_name']}({'+' if item['higher_is_better'] else '-'})"
                    for item in window_result.selection_summary["selected"]
                )
                rejected = [
                    stat
                    for stat in window_result.selection_summary["stats"]
                    if stat["decision"] == "rejected"
                ]
                print(f"    selected: {selected or '(none)'}")
                for stat in rejected:
                    print(f"    rejected: {stat['factor_name']} ({stat['reason']})")
            print(
                f"  {window.window_id} test {window.test_start_date}~"
                f"{window.test_end_date}: return={report.total_return:.4f} "
                f"dd={report.maximum_drawdown:.4f}"
            )

        positive = sum(1 for value in returns if value > 0)
        summary = {
            "window_count": len(returns),
            "positive_windows": positive,
            "mean_return": (
                str(sum(returns, Decimal()) / Decimal(len(returns))) if returns else None
            ),
            "min_return": str(min(returns)) if returns else None,
            "max_return": str(max(returns)) if returns else None,
            "worst_drawdown": str(min(drawdowns)) if drawdowns else None,
        }
        print("\n=== 汇总 ===")
        for key, value in summary.items():
            print(f"  {key}: {value}")

        selection_enabled = args.selection_policy != "none"
        limitations = [
            item
            for item in FACTOR_LIMITATIONS
            if item != "single_window_no_out_of_sample_validation"
        ]
        if selection_enabled:
            limitations += [
                "direction_selected_from_short_training_window",
                "selection_thresholds_authored_not_estimated",
                "window_rejection_changes_evaluated_window_set",
                "selector_calendar_aligned_ic_differs_from_factor_profile_lead_ic",
            ]
        else:
            limitations += ["training_window_does_not_select_parameters"]
        if args.spec_set == "evidence_gated":
            limitations += [
                (
                    "factor_admission_profile_overlaps_test_period"
                    if date.fromisoformat(args.end_date) >= date(2024, 5, 1)
                    else "factor_admission_profile_is_later_than_test_period"
                ),
                "snapshot_screen_filters_not_in_backtest_contract",
            ]
        limitations += ["every_n_anchor_restarts_per_window"]
        payload = {
            **{
                key: getattr(args, key)
                for key in (
                    "start_date",
                    "end_date",
                    "top_n",
                    "spec_set",
                    "exchange",
                    "training_days",
                    "test_days",
                    "step_days",
                    "rebalance_rule",
                    "rebalance_interval",
                    "invested_weight",
                    "selection_policy",
                )
            },
            "portfolio_constraints": (
                None if constraints_request is None else constraints_request.model_dump(mode="json")
            ),
            "factor_version": args.factor_version,
            "research_read_source": settings.research_read_source,
            "interpretation": (
                "link_validation_only"
                if selection_enabled
                else "fixed_rule_window_sensitivity_only"
            ),
            "limitations": limitations,
            "combined_curve": (
                None
                if results.combined is None
                else {
                    "total_return": str(results.combined.total_return),
                    "maximum_drawdown": str(results.combined.maximum_drawdown),
                    "total_commission": str(results.combined.total_commission),
                    "turnover_rate": str(results.combined.turnover_rate),
                }
            ),
            "combined_declarations": results.declarations,
            "windows": windows_payload,
            "summary": summary,
        }
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n已落盘: {args.out}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
