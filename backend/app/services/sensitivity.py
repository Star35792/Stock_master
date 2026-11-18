"""Retrospective selected-run comparisons; no optimizer, reruns or qualification writes."""

import json
import re
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import product
from statistics import median

from pydantic import ValidationError

from app.database.research_session import ResearchSessionLocal
from app.repositories.strategy.sensitivity import SensitivityRepository
from app.schemas.sensitivity import (
    SensitivityCell,
    SensitivityRead,
    SensitivityRequest,
    SensitivitySummary,
)
from app.schemas.strategy import FactorSelectionBacktestRequest
from app.utils.duckdb_write_lock import duckdb_write_lock

SHA = re.compile(r"^[a-f0-9]{64}$")
DERIVED = {
    "signals",
    "symbols",
    "data_version",
    "data_snapshot",
    "strategy_input_snapshot",
    "benchmark_snapshot",
    "execution_events",
    "quantile_backtest",
    "quantile_data_snapshot",
}
REQUIRED = {
    "strategy_name",
    "factor_specs",
    "top_n",
    "allowed_security_types",
    "coverage_ratio",
    "weighting_mode",
    "minimum_score_policy",
    "minimum_score",
    "invested_weight",
    "rebalance_rule",
    "rebalance_interval",
    "portfolio_constraints",
    "signal_lag",
    "holding_period",
    "start_date",
    "end_date",
    "adjust_type",
    "initial_cash",
    "commission_rate",
    "slippage_bps",
    "lot_size",
    "fee_model_version",
    "corporate_action_policy",
    "engine_version",
    "calculation_precision_policy",
    "validation_mode",
    "code_version",
}


def load_sensitivity_runs(run_ids: list[int]) -> tuple[dict[int, dict], dict[int, str]]:
    with (
        duckdb_write_lock(
            "data/research_write.lock", timeout_seconds=3, poll_interval_seconds=0.25
        ),
        ResearchSessionLocal() as db,
    ):
        runs, errors = SensitivityRepository().load(db, run_ids)
        return deepcopy(runs), errors


def canonical(value):
    if isinstance(value, Decimal):
        return "0" if value == 0 else format(value.normalize(), "f")
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def axis_value(parameters: dict, axis: str) -> str | None:
    value = parameters[axis]
    if value is None:
        if axis in {"minimum_score", "coverage_ratio"}:
            return None
        raise ValueError("此扫描轴不得为空。")
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("扫描值必须为精确数值。")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("扫描值不是有效数值。") from exc
    if (
        not number.is_finite()
        or number.copy_abs() > Decimal("1e12")
        or number.as_tuple().exponent < -12
        or len(number.as_tuple().digits) > 24
    ):
        raise ValueError("扫描值非有限或超出范围。")
    if axis in {"top_n", "rebalance_interval"} and (
        number <= 0 or number != number.to_integral_value()
    ):
        raise ValueError("数量与调仓间隔须为正整数。")
    if axis == "rebalance_interval" and number > 250:
        raise ValueError("调仓间隔最多 250 个交易日。")
    if axis in {"coverage_ratio", "invested_weight", "commission_rate"} and not 0 <= number <= 1:
        raise ValueError("比例须在 0 到 1 之间。")
    if axis == "slippage_bps" and not 0 <= number < 10000:
        raise ValueError("滑点须在 0（含）到 10000（不含）之间。")
    if axis in {"coverage_ratio", "invested_weight"} and number == 0:
        raise ValueError("覆盖比例与投入权重须大于零。")
    if axis == "minimum_score" and not 0 <= number <= 1:
        raise ValueError("最低分数须在 0 到 1 之间。")
    return canonical(number)


def flatten(value, prefix="") -> dict:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return result or {prefix: {}}
    return {prefix: canonical(value)}


def identity_reasons(run: dict) -> list[str]:
    p = run["parameters"]
    reasons = []
    if run["strategy_name"] != "factor_selection" or p.get("strategy_name") != "factor_selection":
        reasons.append("首片仅支持普通因子选股合同。")
    if not REQUIRED <= p.keys():
        reasons.append("旧合同缺少固定参数与执行口径字段。")
    nullable = {"coverage_ratio", "minimum_score", "portfolio_constraints"}
    if any(p.get(field) is None for field in REQUIRED - nullable):
        reasons.append("固定合同存在缺失的必填值。")
    try:
        FactorSelectionBacktestRequest.model_validate({"name": "sensitivity", **p})
    except ValidationError:
        reasons.append("记录的因子选股参数不符合支持的合同。")
    if p.get("selection_policy") or p.get("selection_result"):
        reasons.append("训练窗自动选参运行尚不支持受控敏感性。")
    if not run["code_version"] or p.get("code_version") != run["code_version"]:
        reasons.append("运行与合同代码身份缺失或不一致。")
    if (p.get("start_date"), p.get("end_date")) != (
        run["start_date"].isoformat(),
        run["end_date"].isoformat(),
    ):
        reasons.append("运行日期与合同不一致。")
    market = p.get("data_snapshot")
    factor = p.get("strategy_input_snapshot")
    if not isinstance(market, dict) or not (
        market.get("snapshot_version") == "trusted_market_slice_v1"
        and market.get("verification_status") == "verified"
        and market.get("quality_status") == "trusted"
        and SHA.fullmatch(str(market.get("sha256", "")))
        and market.get("sha256") == market.get("post_run_sha256")
    ):
        reasons.append("缺少已核验市场组成摘要。")
    if not isinstance(factor, dict) or not (
        factor.get("contract_version") == "strategy_factor_frame_v1"
        and SHA.fullmatch(str(factor.get("sha256", "")))
        and type(factor.get("row_count")) is int
        and factor["row_count"] > 0
    ):
        reasons.append("缺少因子输入身份。")
    if p.get("benchmark"):
        benchmark = p.get("benchmark_snapshot")
        if not isinstance(benchmark, dict) or not (
            benchmark.get("status") == "verified"
            and SHA.fullmatch(str(benchmark.get("sha256", "")))
        ):
            reasons.append("缺少已冻结基准摘要。")
    return reasons


def performance_reasons(run: dict) -> list[str]:
    if run["status"] != "success" or run["total_return"] is None:
        return ["运行未成功或缺少已保存绩效，不派生或回填指标。"]
    metrics = [run[name] for name in ("total_return", "maximum_drawdown", "sharpe_ratio")]
    if any(
        value is not None and (not isinstance(value, Decimal) or not value.is_finite())
        for value in metrics
    ):
        return ["已保存指标含无效精度或非有限值。"]
    if run["run_total_return"] != run["total_return"]:
        return ["运行收益与已保存绩效收益不一致。"]
    if not run["performance_code_version"]:
        return ["缺少已保存绩效的计算版本。"]
    return []


def fixed_contract(run: dict, axes: list[str], *, across_windows: bool = False) -> dict:
    omitted = DERIVED | set(axes)
    if across_windows:
        omitted |= {
            "start_date",
            "end_date",
            "test_start_date",
            "test_end_date",
            "training_start_date",
            "training_end_date",
            "validation_window_id",
        }
    return flatten({k: v for k, v in run["parameters"].items() if k not in omitted})


def differences(left: dict, right: dict) -> list[str]:
    return sorted(
        k
        for k in left.keys() | right.keys()
        if (k in left) != (k in right) or left.get(k) != right.get(k)
    )


class SensitivityService:
    def __init__(self, loader: Callable = load_sensitivity_runs):
        self.loader = loader

    def evaluate(self, payload: SensitivityRequest) -> SensitivityRead:
        runs, errors = self.loader(payload.run_ids)  # loader closes lock/session before compute
        groups = defaultdict(list)
        values_by_key, folds_by_key, classifications = {}, {}, {}
        for run_id in payload.run_ids:
            if run_id not in runs:
                continue
            run = runs[run_id]
            try:
                values = tuple(axis_value(run["parameters"], axis) for axis in payload.axes)
            except (ValueError, KeyError, TypeError):
                errors[run_id] = "所选扫描轴在该运行中缺失或不是支持的精确数值。"
                continue
            key = json.dumps(values)
            window = (run["start_date"], run["end_date"])
            values_by_key[key] = list(values)
            folds_by_key[window] = window
            groups[(key, window)].append(run_id)
            classifications[run_id] = key
        # Explicitly preserve unobserved combinations of two declared axes.
        if len(payload.axes) == 2 and values_by_key:
            dimensions = [
                sorted(
                    {v[i] for v in values_by_key.values()},
                    key=lambda v: (v is not None, Decimal(v) if v else Decimal(0)),
                )
                for i in range(2)
            ]
            if len(dimensions[0]) * len(dimensions[1]) * len(folds_by_key) > 256:
                raise ValueError("候选×窗口超过 256 个单元，请缩小选择范围。")
            values_by_key = {json.dumps(pair): list(pair) for pair in product(*dimensions)}
        candidate_keys = sorted(
            values_by_key,
            key=lambda key: tuple(
                (v is not None, Decimal(v) if v else Decimal(0)) for v in values_by_key[key]
            ),
        )
        windows = sorted(folds_by_key)
        # Product of declared selected candidates/windows is bounded independently of run count.
        if len(candidate_keys) * len(windows) > 256:
            raise ValueError("候选×窗口超过 256 个单元，请缩小选择范围。")
        candidate_ids = {key: f"C{i + 1}" for i, key in enumerate(candidate_keys)}
        fold_ids = {window: f"F{i + 1}" for i, window in enumerate(windows)}
        baseline_key = classifications.get(payload.baseline_run_id)
        cells = []
        for key in candidate_keys:
            for window in windows:
                ids = sorted(groups.get((key, window), []))
                cell = SensitivityCell(
                    candidate=candidate_ids[key],
                    fold=fold_ids[window],
                    run_ids=ids,
                    axis_values=values_by_key[key],
                    status="missing",
                    reasons=["所选运行缺少此格。"],
                )
                cells.append(cell)
                if len(ids) > 1:
                    cell.status, cell.reasons = (
                        "duplicate",
                        ["同候选同窗口有多个运行，不自动挑最好一条。"],
                    )
                    continue
                if not ids:
                    continue
                run = runs[ids[0]]
                invalid_performance = performance_reasons(run)
                if invalid_performance:
                    cell.status = "unavailable"
                    cell.reasons = invalid_performance
                    continue
                metrics = [
                    run[name] for name in ("total_return", "maximum_drawdown", "sharpe_ratio")
                ]
                if any(
                    value is not None and (not isinstance(value, Decimal) or not value.is_finite())
                    for value in metrics
                ):
                    cell.status, cell.reasons = "unavailable", ["已保存指标含无效精度或非有限值。"]
                    continue
                cell.total_return, cell.maximum_drawdown, cell.sharpe_ratio = metrics
                reasons = identity_reasons(run)
                baseline_ids = groups.get((baseline_key, window), [])
                if len(baseline_ids) != 1:
                    reasons.append("此窗口缺少唯一的基准候选运行。")
                else:
                    baseline = runs[baseline_ids[0]]
                    reasons.extend(identity_reasons(baseline))
                    reasons.extend(f"基准：{reason}" for reason in performance_reasons(baseline))
                    if (
                        run["strategy_name"],
                        run["code_version"],
                        run["performance_code_version"],
                    ) != (
                        baseline["strategy_name"],
                        baseline["code_version"],
                        baseline["performance_code_version"],
                    ) or not run["performance_code_version"]:
                        reasons.append("策略、运行代码或绩效计算版本不同/缺失。")
                    if run["initial_cash"] != baseline["initial_cash"]:
                        reasons.append("期初资金不同。")
                    changed = differences(
                        fixed_contract(run, payload.axes), fixed_contract(baseline, payload.axes)
                    )
                    anchor = runs[payload.baseline_run_id]
                    cross_changed = differences(
                        fixed_contract(run, payload.axes, across_windows=True),
                        fixed_contract(anchor, payload.axes, across_windows=True),
                    )
                    if cross_changed or (run["code_version"], run["performance_code_version"]) != (
                        anchor["code_version"],
                        anchor["performance_code_version"],
                    ):
                        reasons.append("跨窗口固定合同或代码版本改变。")
                        changed = sorted(set(changed + cross_changed))
                    cell.contract_differences = changed[:30]
                    if changed:
                        reasons.append("存在非扫描参数差异，不作单轴/双轴归因。")
                    for field, label in [
                        ("data_snapshot", "市场"),
                        ("strategy_input_snapshot", "因子"),
                        ("benchmark_snapshot", "基准"),
                    ]:
                        if canonical(run["parameters"].get(field)) != canonical(
                            baseline["parameters"].get(field)
                        ):
                            reasons.append(f"{label}组成身份不同，旧整体摘要不能证明共同输入相同。")
                    if not reasons and baseline["total_return"] is not None:
                        cell.delta_return = run["total_return"] - baseline["total_return"]
                cell.reasons = list(dict.fromkeys(reasons))
                cell.status = "exploratory" if reasons else "controlled"
        summaries = []
        for key in candidate_keys:
            selected = [cell for cell in cells if cell.candidate == candidate_ids[key]]
            controlled = [cell.total_return for cell in selected if cell.status == "controlled"]
            complete = len(controlled) == len(windows) and bool(windows) and not errors
            summaries.append(
                SensitivitySummary(
                    candidate=candidate_ids[key],
                    axis_values=values_by_key[key],
                    complete=complete,
                    controlled_folds=len(controlled),
                    expected_folds=len(windows),
                    median_return=median(controlled) if complete else None,
                    worst_return=min(controlled) if complete else None,
                    return_range=max(controlled) - min(controlled) if complete else None,
                )
            )
        frozen = {
            "request": payload.model_dump(),
            "runs": runs,
            "errors": errors,
            "version": "selected_run_sensitivity_v1",
        }
        digest = sha256(
            json.dumps(frozen, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return SensitivityRead(
            generated_at=datetime.now(UTC),
            request=payload,
            contract_sha256=digest,
            baseline_candidate=candidate_ids.get(baseline_key),
            folds={fold_ids[w]: w for w in windows},
            candidates={candidate_ids[k]: values_by_key[k] for k in candidate_keys},
            cells=cells,
            summaries=summaries,
            unclassified_run_ids=sorted(errors),
            unclassified_reasons=errors,
            declarations=[
                "只比较所选运行形成的候选×窗口网格；未选择的候选/窗口不可视为不存在。",
                "受控仅表示记录的固定合同及输入摘要一致，不认证完整 PIT 或策略收益资格。",
                "失败、缺绩效、缺格、重复格均保留；不填零、不选最好一条、不补跑回测。",
                "不同窗口的收益不复利拼接；中位/最差/范围是描述统计，不自动排序或选冠军。",
                "本次假设与接受/停止说明是事后记录，不能冒充历史预注册或未使用的留出集。",
                "报告在内存生成，不回写原运行或晋级策略；全部后续尝试仍应计入研究选择历史。",
            ],
        )
