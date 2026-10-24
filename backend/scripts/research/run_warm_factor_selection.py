"""通过平台研究链路在温层上跑一次因子选股回测。

本脚本刻意写明全部偏差与未覆盖项；任何数字都必须结合这些限制解读。

已知限制（本次运行全部成立）
---------------------------
1. **公司行为事件会计未实现**：回测对区间内命中公司行为的证券整次拒绝，因此
   本次只使用 2025-11-03~2026-04-30 这一公司行为稀疏窗口，且股票池已经被
   ``backfill_warm_factors.py --exclude-corporate-actions`` 剔除了窗口内有
   除权除息/送转的 606 只证券。这构成真实的筛选偏差（红利支付者被系统性剔除），
   结果不代表全市场。
2. **幸存者偏差未修正**：``market.security`` 7,728 只证券全部 ``is_active=True``、
   ``delist_date`` 全为空，退市证券不在样本内。
3. **行业时点缺失**：``market.security_industry_history`` 为 0 行，本次未使用
   行业中性化。
4. **停牌事件表为空**：``market.security_status_event`` 0 行，停牌仍依赖
   「当日无 bar」推断。
5. **样本区间单一**：仅一段 6 个月窗口，无样本外滚动验证。

因此本脚本产出的收益数字只能用于观察因子选股链路是否可复现、执行/成本/可交易性
约束是否生效，**不得表述为可信的策略收益**。
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

# 研究读路径必须在导入 app 之前切换，settings 在导入时固化取值。
os.environ.setdefault("RESEARCH_READ_SOURCE", "warm")

# 回测用的因子版本 v1-qfq 只存在于 20261022-181648 这个 release；更新的 release
# （如只含 v2-qfq-2y 的 IC 证据 release）会让 backtest 找不到因子值，因此这里
# 预扫描 --release-tag 并在 settings 固化前写入环境变量。
_PRE_PARSER = argparse.ArgumentParser(add_help=False)
_PRE_PARSER.add_argument("--release-tag", default="20261022-181648")
_PRE_ARGS, _ = _PRE_PARSER.parse_known_args()
os.environ.setdefault("RESEARCH_RELEASE_TAG", _PRE_ARGS.release_tag)

from sqlalchemy.orm import Session  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.database.research_session import (  # noqa: E402
    ResearchSessionLocal,
    initialize_research_database,
)
from app.database.session import SessionLocal  # noqa: E402
from app.portfolio import PortfolioConstraints  # noqa: E402
from app.schemas.strategy import (  # noqa: E402
    BacktestPortfolioConstraintsRequest,
    FactorScoreSpecRequest,
    FactorSelectionBacktestRequest,
    WeightingMode,
)
from app.services.performance import BacktestPerformanceService  # noqa: E402
from app.services.strategy_research import StrategyResearchService  # noqa: E402
from app.strategies import FactorScoreSpec  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

FACTOR_VERSION = "v1-qfq"

#: (因子名, 权重, 方向)。组合为「动量 + 低波 + 高流动性 + 低回撤」。
DEFAULT_SPECS = [
    ("momentum_20", Decimal("1"), True),
    ("momentum_60", Decimal("1"), True),
    ("volatility_20", Decimal("1"), False),
    ("downside_volatility_20", Decimal("1"), False),
    ("max_drawdown_20", Decimal("1"), True),
    ("liquidity_20", Decimal("1"), True),
    ("volume_stability_20", Decimal("1"), True),
]

#: 按已落库的 IC 证据（``v2-qfq-2y``，2024-05-01~2026-04-30，483 个交易日）
#: 修正方向后的预设。RankIC1 实测：
#:
#: * ``volume_stability_20`` +0.0435（IR 0.60，正相关率 72.5%）→ 越高越好
#: * ``max_drawdown_20``     +0.0179（IR 0.09）→ 越高（回撤越浅）越好
#: * ``volatility_20``       -0.0523 → 越低越好
#: * ``downside_volatility_20`` -0.0333 → 越低越好
#: * ``momentum_20``         -0.0505（IR -0.33，正相关率仅 36.0%）→ **应改为越低越好**
#: * ``momentum_60``         -0.0408 → **应改为越低越好**
#: * ``liquidity_20``        -0.0401 → **应改为越低越好**
#:
#: 即默认预设里动量与流动性三个因子的方向与实测效应相反，且 |IR| 在 0.26~0.33
#: 之间并非噪声。本预设只翻转方向，权重仍为等权。
IC_ALIGNED_SPECS = [
    ("momentum_20", Decimal("1"), False),
    ("momentum_60", Decimal("1"), False),
    ("volatility_20", Decimal("1"), False),
    ("downside_volatility_20", Decimal("1"), False),
    ("max_drawdown_20", Decimal("1"), True),
    ("liquidity_20", Decimal("1"), False),
    ("volume_stability_20", Decimal("1"), True),
]

SPEC_SETS = {"default": DEFAULT_SPECS, "ic_aligned": IC_ALIGNED_SPECS}

#: 宽候选池：温层回填的全部 13 个价量因子，先验方向一律用平台
#: ``basic_price_volume_factors`` 的文档默认方向（不含全样本 IC 知识），
#: 供 ``--selection-policy training_ic_v1`` 逐窗自动选参使用；权重不参与选择。
PRICE_VOLUME_WIDE_SPECS = [
    ("ret_1d", Decimal("1"), True),
    ("momentum_5", Decimal("1"), True),
    ("reversal_5", Decimal("1"), True),
    ("volatility_5", Decimal("1"), False),
    ("ma_gap_5", Decimal("1"), True),
    ("volume_chg_1d", Decimal("1"), True),
    ("momentum_20", Decimal("1"), True),
    ("momentum_60", Decimal("1"), True),
    ("volatility_20", Decimal("1"), False),
    ("downside_volatility_20", Decimal("1"), False),
    ("max_drawdown_20", Decimal("1"), True),
    ("liquidity_20", Decimal("1"), True),
    ("volume_stability_20", Decimal("1"), True),
]
SPEC_SETS["price_volume_wide"] = PRICE_VOLUME_WIDE_SPECS

FACTOR_LIMITATIONS = [
    "corporate_action_event_accounting_not_implemented",
    "universe_excludes_symbols_with_corporate_actions_in_window",
    "survivorship_bias_not_corrected",
    "industry_point_in_time_labels_unavailable",
    "suspension_events_not_populated",
    "single_window_no_out_of_sample_validation",
]


def build_request(args: argparse.Namespace) -> FactorSelectionBacktestRequest:
    specs = [
        FactorScoreSpecRequest(
            factor_name=name,
            factor_version=FACTOR_VERSION,
            weight=weight,
            higher_is_better=higher,
        )
        for name, weight, higher in SPEC_SETS[args.spec_set]
    ]
    return FactorSelectionBacktestRequest(
        name=args.name,
        strategy_name="factor_selection",
        factor_specs=specs,
        top_n=args.top_n,
        signal_lag=1,
        rebalance_rule=args.rebalance_rule,
        rebalance_interval=args.rebalance_interval,
        holding_period=1,
        benchmark=args.benchmark,
        allowed_security_types={"stock"},
        coverage_ratio=Decimal("0.8"),
        weighting_mode=WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED,
        portfolio_constraints=(
            BacktestPortfolioConstraintsRequest(
                max_symbol_weight=Decimal(args.max_symbol_weight),
                max_industry_weight=Decimal(args.max_industry_weight),
                cash_reserve_rate=Decimal(args.cash_reserve_rate),
            )
            if args.enable_portfolio_constraints
            else None
        ),
        adjust_type="qfq",
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        initial_cash=Decimal(args.initial_cash),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        fee_model_version="a_share_cost_v1",
        min_commission=Decimal("5"),
        sell_stamp_duty_rate=Decimal("0.0005"),
        transfer_fee_rate=Decimal("0.00001"),
        exchange_fee_rate=Decimal("0.0000487"),
        code_version=args.code_version,
        data_version=args.data_version,
        validation_mode="none",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="温层因子选股回测")
    parser.add_argument("--start-date", default="2025-11-03")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--spec-set",
        choices=sorted(SPEC_SETS),
        default="default",
        help="因子方向预设；ic_aligned 按已落库 IC 证据翻转反向因子",
    )
    parser.add_argument("--release-tag", default="20261022-181648")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--benchmark", default=None)
    parser.add_argument(
        "--rebalance-rule",
        choices=["daily", "weekly", "monthly", "every_n_trading_days"],
        default="daily",
        help="调仓频率；every_n_trading_days 需配 --rebalance-interval",
    )
    parser.add_argument(
        "--rebalance-interval",
        type=int,
        default=1,
        help="every_n_trading_days 的交易日间隔（如 20 = 每 20 个交易日调仓）",
    )
    parser.add_argument(
        "--enable-portfolio-constraints",
        action="store_true",
        help="启用与 paper 链路同源的组合约束（单标/行业/现金留存）",
    )
    parser.add_argument("--max-symbol-weight", default="0.20")
    parser.add_argument("--max-industry-weight", default="0.40")
    parser.add_argument("--cash-reserve-rate", default="0.05")
    parser.add_argument("--name", default=f"warm-factor-selection-{datetime.now():%Y%m%d-%H%M%S}")
    parser.add_argument("--code-version", default="local")
    parser.add_argument("--data-version", default="warm-20261022-175052")
    parser.add_argument("--out", help="结果 JSON 落盘路径")
    args = parser.parse_args(argv)

    print(f"research_read_source = {settings.research_read_source}")
    print(f"窗口 {args.start_date} ~ {args.end_date}, top_n={args.top_n}")

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
        request = build_request(args)
        specs = [
            FactorScoreSpec(
                factor_name=spec.factor_name,
                factor_version=spec.factor_version,
                weight=spec.weight,
                higher_is_better=spec.higher_is_better,
            )
            for spec in request.factor_specs
        ]
        result = StrategyResearchService().run_factor_selection(
            db,
            name=request.name,
            strategy_name=request.strategy_name,
            factor_specs=specs,
            top_n=request.top_n,
            signal_lag=request.signal_lag,
            rebalance_rule=request.rebalance_rule,
            holding_period=request.holding_period,
            rebalance_interval=request.rebalance_interval,
            portfolio_constraints=(
                None
                if request.portfolio_constraints is None
                else PortfolioConstraints(
                    max_symbol_weight=request.portfolio_constraints.max_symbol_weight,
                    max_industry_weight=request.portfolio_constraints.max_industry_weight,
                    cash_reserve_rate=request.portfolio_constraints.cash_reserve_rate,
                    security_type_budgets=request.portfolio_constraints.security_type_budgets,
                )
            ),
            benchmark=request.benchmark,
            allowed_security_types=request.allowed_security_types,
            coverage_ratio=request.coverage_ratio,
            weighting_mode=request.weighting_mode,
            adjust_type=request.adjust_type,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
            commission_rate=request.commission_rate,
            slippage_bps=request.slippage_bps,
            lot_size=request.lot_size,
            fee_model_version=request.fee_model_version,
            min_commission=request.min_commission,
            sell_stamp_duty_rate=request.sell_stamp_duty_rate,
            transfer_fee_rate=request.transfer_fee_rate,
            exchange_fee_rate=request.exchange_fee_rate,
            research_db=research_db,
            code_version=request.code_version,
            data_version=request.data_version,
        )
        research_db.commit()
        print(f"\nrun_id = {result.run_id}")

        report = BacktestPerformanceService().analyze(
            db,
            research_db=research_db,
            run_id=result.run_id,
            code_version=args.code_version,
            style_factors=None,
        )
        performance = report.report
        payload = {
            "run_id": result.run_id,
            "window": [args.start_date, args.end_date],
            "top_n": args.top_n,
            "rebalance_rule": args.rebalance_rule,
            "rebalance_interval": args.rebalance_interval,
            "portfolio_constraints": (
                None
                if request.portfolio_constraints is None
                else request.portfolio_constraints.model_dump(mode="json")
            ),
            "factors": [
                {"name": name, "weight": str(w), "higher_is_better": h}
                for name, w, h in SPEC_SETS[args.spec_set]
            ],
            "spec_set": args.spec_set,
            "factor_version": FACTOR_VERSION,
            "research_read_source": settings.research_read_source,
            "limitations": FACTOR_LIMITATIONS,
            "performance": {
                "total_return": str(performance.total_return),
                "annualized_return": str(performance.annualized_return),
                "maximum_drawdown": str(performance.maximum_drawdown),
                "annualized_volatility": str(performance.annualized_volatility),
                "sharpe_ratio": str(performance.sharpe_ratio)
                if performance.sharpe_ratio is not None
                else None,
                "turnover_rate": str(performance.turnover_rate),
                "total_commission": str(performance.total_commission),
                "daily_win_rate": str(performance.daily_win_rate),
                "calmar_ratio": str(performance.calmar_ratio)
                if performance.calmar_ratio is not None
                else None,
                "profit_loss_ratio": str(performance.profit_loss_ratio)
                if performance.profit_loss_ratio is not None
                else None,
            },
        }
        print("\n=== 绩效 ===")
        for key, value in payload["performance"].items():
            print(f"  {key}: {value}")
        if args.out:
            Path(args.out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n已落盘: {args.out}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
