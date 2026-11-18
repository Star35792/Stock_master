"""Bounded prefix-invariance checks; callbacks must recompute their stated scope."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from math import isfinite
from time import monotonic
from typing import Literal

from app.backtest.engine import TargetWeightSignal

AUDIT_VERSION = "signal_prefix_v1"
MAX_ROWS = 20_000
MAX_SIGNALS = 20_000
MAX_DATES = 252
MAX_FEATURES = 8
MAX_EXAMPLES = 12
MAX_SECONDS = 10
AuditStatus = Literal["passed", "failed", "insufficient", "error"]


@dataclass(frozen=True)
class AuditObservation:
    trade_date: date
    symbol: str
    feature: str
    value: Decimal | None


@dataclass(frozen=True)
class AuditDifference:
    signal_date: date
    symbol: str
    expected: str | None
    actual: str | None


@dataclass(frozen=True)
class AuditCheck:
    kind: str
    cutoff: date
    compared_values: int
    differences: int
    changed_input_rows: int = 0
    examples: list[AuditDifference] = field(default_factory=list)


@dataclass
class SignalAuditResult:
    status: AuditStatus = "insufficient"
    reason: str = "没有足够的可比较信号。"
    version: str = AUDIT_VERSION
    input_rows: int = 0
    signal_count: int = 0
    evaluations: int = 0
    cutoffs: list[date] = field(default_factory=list)
    checks: list[AuditCheck] = field(default_factory=list)


SignalEvaluator = Callable[[tuple[AuditObservation, ...]], Iterable[TargetWeightSignal]]


def canonical_signals(signals: Iterable[TargetWeightSignal]) -> dict[tuple[date, str], Decimal]:
    """Presence matters: an absent signal is different from an explicit zero exit."""
    result: dict[tuple[date, str], Decimal] = {}
    for index, signal in enumerate(signals):
        if index >= MAX_SIGNALS:
            raise ValueError("信号数量超过审计上限。")
        if type(signal.signal_date) is not date or not signal.symbol.strip():
            raise ValueError("信号日期或证券无效。")
        value = signal.target_weight
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("信号权重必须为有限 Decimal，不得用零替代无效值。")
        key = (signal.signal_date, signal.symbol.strip().upper())
        if key in result:
            raise ValueError("同一日期和证券存在重复信号。")
        result[key] = value
    return result


def _compare(
    expected: dict[tuple[date, str], Decimal],
    actual: dict[tuple[date, str], Decimal],
    *,
    kind: str,
    cutoff: date,
    example_budget: int,
) -> AuditCheck:
    keys = sorted(key for key in expected.keys() | actual.keys() if key[0] <= cutoff)
    changed = [key for key in keys if expected.get(key) != actual.get(key)]
    return AuditCheck(
        kind=kind,
        cutoff=cutoff,
        compared_values=len(keys),
        differences=len(changed),
        examples=[
            AuditDifference(
                signal_date=key[0], symbol=key[1],
                expected=str(expected[key]) if key in expected else None,
                actual=str(actual[key]) if key in actual else None,
            )
            for key in changed[: min(4, example_budget)]
        ],
    )


def audit_signals(
    observations: Iterable[AuditObservation],
    evaluate: SignalEvaluator,
    *,
    expected_signals: Iterable[TargetWeightSignal] | None = None,
    time_budget: float = MAX_SECONDS,
) -> SignalAuditResult:
    """At most 9 evaluations and 3 cutoffs; the deadline is cooperative, not a kill switch."""
    result = SignalAuditResult()
    try:
        if not isfinite(time_budget) or time_budget <= 0:
            result.reason = "计算时间预算必须为有限正数。"
            return result
        deadline = monotonic() + min(time_budget, MAX_SECONDS)
        rows: list[AuditObservation] = []
        keys: set[tuple[date, str, str]] = set()
        for index, row in enumerate(observations):
            if index >= MAX_ROWS:
                result.reason = "输入超过 20000 行审计上限，未截取部分数据冒充完整审计。"
                return result
            if type(row.trade_date) is not date or not row.symbol or not row.feature:
                raise ValueError("观察值的日期、证券和特征不能为空。")
            if row.value is not None and (
                not isinstance(row.value, Decimal) or not row.value.is_finite()
            ):
                raise ValueError("观察值必须是有限 Decimal 或明确缺失值。")
            key = (row.trade_date, row.symbol, row.feature)
            if key in keys:
                raise ValueError("输入包含重复日期、证券和特征。")
            keys.add(key)
            rows.append(row)
        result.input_rows = len(rows)
        dates = sorted({row.trade_date for row in rows})
        if len(dates) < 2:
            result.reason = "至少需要两个不同日期，才能改变未来输入并比较过去信号。"
            return result
        if len(dates) > MAX_DATES or len({row.feature for row in rows}) > MAX_FEATURES:
            result.reason = "输入日期或特征数量超过审计上限。"
            return result
        frozen = tuple(sorted(rows, key=lambda row: (row.trade_date, row.symbol, row.feature)))

        def run(data: tuple[AuditObservation, ...]) -> dict[tuple[date, str], Decimal]:
            if monotonic() > deadline:
                raise TimeoutError
            result.evaluations += 1
            signals = canonical_signals(evaluate(data))
            if monotonic() > deadline:
                raise TimeoutError
            if any(key[0] < dates[0] or key[0] > dates[-1] for key in signals):
                raise ValueError("回调产生了审计窗口之外的信号。")
            return signals

        baseline = run(frozen)
        result.signal_count = len(baseline)
        if expected_signals is not None and baseline != canonical_signals(expected_signals):
            result.reason = "当前重建信号与原运行完整信号不一致，不能核验该运行。"
            return result

        remaining_examples = MAX_EXAMPLES

        def check(kind: str, cutoff: date, data: tuple[AuditObservation, ...]) -> AuditCheck:
            nonlocal remaining_examples
            item = _compare(
                baseline, run(data), kind=kind, cutoff=cutoff,
                example_budget=remaining_examples,
            )
            if kind in {"truncate", "perturb"}:
                item = replace(item, changed_input_rows=sum(
                    original != modified for original, modified in zip(frozen, data, strict=False)
                ) + abs(len(frozen) - len(data)))
            remaining_examples -= len(item.examples)
            result.checks.append(item)
            return item

        if check("repeat_before", dates[-1], frozen).differences:
            result.status = "error"
            result.reason = "相同输入重算结果不一致，不能归因为未来数据依赖。"
            return result
        candidates = dates[:-1]
        result.cutoffs = [candidates[i] for i in sorted({0, (len(candidates) - 1) // 2,
                                                       len(candidates) - 1})]
        for cutoff in result.cutoffs:
            check("truncate", cutoff, tuple(row for row in frozen if row.trade_date <= cutoff))
            changed = tuple(
                replace(row, value=-row.value * Decimal("1.37") + Decimal(index + 1) / 100)
                if row.trade_date > cutoff and row.value is not None else row
                for index, row in enumerate(frozen)
            )
            check("perturb", cutoff, changed)
        if check("repeat_after", dates[-1], frozen).differences:
            result.status = "error"
            result.reason = "重复输入控制检查不稳定，无法将差异解释为未来数据依赖。"
        else:
            variants = [item for item in result.checks if item.kind in {"truncate", "perturb"}]
            if any(item.differences for item in variants):
                result.status = "failed"
                result.reason = "改变未来输入后，过去信号发生变化，请复核对应逻辑。"
            elif any(item.compared_values and item.changed_input_rows for item in variants):
                result.status = "passed"
                result.reason = "所抽查的信号前缀未发现变化；通过不代表完整时点或收益认证。"
        return result
    except TimeoutError:
        result.status = "insufficient"
        result.reason = "达到计算时间预算，检查未完成。"
        return result
    except Exception as exc:
        result.status = "error"
        result.reason = f"审计回调或输入校验失败（{type(exc).__name__}）：{exc}"
        return result
