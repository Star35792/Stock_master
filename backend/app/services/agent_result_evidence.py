"""Small observations of original service results, never a second business ledger."""

import json
from datetime import UTC, datetime


def _scalars(value, fields):
    return {
        key: value[key]
        for key in fields
        if key in value
        and (value[key] is None or type(value[key]) in {str, int, bool})
        and len(str(value[key])) <= 200
    }


def execution_context(session):
    """Saved observations are context, never permission or a current market quote."""
    reports = []
    for plan in session.plans:
        execution = session.executions.get(plan.plan_id)
        if execution is None:
            continue
        report = {
            "plan_id": plan.plan_id,
            "version": plan.version,
            "plan_status": plan.status,
            "execution_status": execution.status,
            "goal_verified": False,
            "steps": [
                {
                    "step_id": step.step_id,
                    "status": step.status,
                    "task_id": step.task_id,
                    "summary": step.summary,
                }
                for step in execution.steps
            ],
        }
        reports.append(report)
    encoded = json.dumps(reports, ensure_ascii=False)
    if len(encoded.encode()) > 32000:
        return json.dumps(
            {
                "unavailable": True,
                "note": "执行证据超过上下文上限，请查看计划结果或缩小范围。",
            },
            ensure_ascii=False,
        )
    return encoded


def result_evidence(value):
    """Input has already passed json_ready: Decimal values remain exact strings."""
    if not isinstance(value, dict):
        return None
    facts, limits, source = {}, [], {}
    if isinstance(value.get("run"), dict):
        run = value["run"]
        source = {"kind": "backtest", "id": run.get("id")}
        facts = _scalars(
            run,
            (
                "status",
                "start_date",
                "end_date",
                "initial_cash",
                "final_equity",
                "total_return",
                "code_version",
                "data_version",
            ),
        )
        contract = run.get("parameters") or {}
        facts.update(
            _scalars(
                contract,
                (
                    "validation_mode",
                    "training_artifact_verified",
                    "fee_model_version",
                    "adjust_type",
                ),
            )
        )
        facts["evidence_qualified"] = (
            contract["evidence_qualified"]
            if type(contract.get("evidence_qualified")) is bool
            else None
        )
        performance = value.get("performance")
        if isinstance(performance, dict):
            facts.update(
                _scalars(
                    performance,
                    (
                        "total_return",
                        "annualized_return",
                        "maximum_drawdown",
                        "sharpe_ratio",
                        "total_commission",
                        "turnover_rate",
                    ),
                )
            )
        else:
            limits.append("绩效指标尚不可用，不能用任务成功补造指标。")
        holdings = value.get("final_holdings")
        if isinstance(holdings, dict):
            facts.update(_scalars(holdings, ("as_of_date", "total_equity")))
            facts["holding_count"] = len(holdings.get("holdings") or [])
        if not contract.get("benchmark"):
            limits.append("原回测未配置基准，未计算超额收益。")
        limits.append("历史研究结果；任务成功或身份核验不代表策略认证、收益保证或研究目标达成。")
        limits.append(
            "本查询不认证证据资格；PIT、历史ST/退市、公司行为覆盖及行业、因子首次可得时点须按原研究证据核对。"
        )
    elif "book_id" in value and isinstance(value.get("holdings"), list):
        source = {"kind": "positions", "id": value["book_id"]}
        facts = _scalars(
            value,
            (
                "price_as_of",
                "price_stale",
                "sequence_anomaly",
                "cash",
                "positions_market_value",
                "total_equity",
                "realized_pnl",
            ),
        )
        rows = value["holdings"]
        dates = sorted({row["price_as_of"] for row in rows if row.get("price_as_of")})
        facts.update(
            {
                "holding_count": len(rows),
                "missing_price_count": sum(row.get("price") is None for row in rows),
                "earliest_price_date": dates[0] if dates else None,
                "latest_price_date": dates[-1] if dates else None,
            }
        )
        limits.append("只读人工账本；各证券报价日期可能不同，汇总日期不代表同日完整估值。")
        if facts["missing_price_count"] or value.get("price_stale"):
            limits.append("缺可信报价的持仓按原服务成本估值，总值不等于完整可信市值。")
        if value.get("sequence_anomaly"):
            limits.append("存在流水时序异常，请在人工账本核对。")
    elif "plan_id" in value and isinstance(value.get("targets"), list):
        source = {"kind": "portfolio", "id": value["plan_id"]}
        facts = _scalars(
            value,
            (
                "as_of_date",
                "status",
                "execution_allowed",
                "input_source",
                "total_equity",
                "cash_before",
                "target_cash",
                "cash_after",
                "code_version",
                "source_run_id",
            ),
        )
        facts["target_count"] = len(value["targets"])
        if value.get("input_source") == "agent_backtest_prefill":
            limits.append("历史回测预算、零现仓假设的研究草案，不能作为当前真实持仓或直接执行。")
    elif "symbol" in value and "data_status" in value:
        source = {"kind": "market", "symbol": value["symbol"]}
        facts = _scalars(value, ("symbol", "latest_trade_date", "data_status"))
        facts.update(
            _scalars(
                value.get("latest") or {},
                (
                    "close",
                    "adjust_type",
                    "quality_status",
                    "source_name",
                ),
            )
        )
        limits.append("可信日线收盘数据，非实时行情；无报价时不能推断价格。")
    elif value.get("source_name") == "tdx_official" and isinstance(value.get("items"), list):
        source = {"kind": "market", "symbol": value["symbol"]}
        facts = _scalars(value, ("symbol", "trade_date", "adjust_type", "source_name", "total"))
        rows = value["items"]
        facts["compared_sample_count"] = len(rows)
        facts["conflict_sample_count"] = sum(row.get("is_conflict") is True for row in rows)
        if len(rows) == 1:
            facts.update(_scalars(rows[0], (
                "source_a", "source_b", "raw_id_a", "raw_id_b", "close_diff", "conflict_reason",
            )))
        limits.append("仅查询已有的通达信未复权日线对账；空样本表示缺少对账记录。")
    elif "action_id" in value and "action_type" in value:
        source = {"kind": "operations", "id": value["action_id"]}
        facts = _scalars(
            value,
            (
                "action_type",
                "status",
                "phase",
                "started_at",
                "finished_at",
            ),
        )
        facts.update(_scalars(value.get("parameters") or {}, ("start_date", "end_date")))
        result = value.get("result") or {}
        quote = result.get("quote_observation")
        if value["action_type"] == "tencent_quote_capture" and isinstance(quote, dict):
            facts.update(_scalars(quote, (
                "symbol", "source_name", "observed_at", "quote_time_verified",
                "observation_status", "price_cny", "pe_ttm", "pb", "batch_id",
                "raw_snapshot_id", "raw_batch_id",
            )))
            limits.append("腾讯报价快照仅供查看。采集时间与报价时间分开，报价时点和鲜度未核实。")
        facts.update(
            _scalars(result, ("problem_closed", "quality_issue_updated", "daily_loop_job_id"))
        )
        facts.update(_scalars(result.get("sync") or {}, ("completed_through",)))
        recheck = result.get("recheck") or {}
        facts.update(
            _scalars(
                recheck,
                (
                    "scope",
                    "profile_rows",
                    "failed_count",
                    "empty_factor_count",
                    "unresolved_rows",
                    "target_trade_date",
                    "factor_rows",
                    "daily_loop_status",
                    "gate_status",
                    "publish_eligible",
                    "market_release_id",
                    "curated_release_id",
                    "alternative_status",
                ),
            )
        )
        if isinstance(recheck.get("candidates"), list):
            facts["remaining_gap_count"] = len(recheck["candidates"])
        for key, label in (
            ("failed_services", "失败的因子族"),
            ("empty_services", "无产物的因子族"),
            ("reason_codes", "原任务原因码"),
        ):
            values = recheck.get(key)
            if isinstance(values, list):
                facts[key + "_count"] = len(values)
                if values:
                    codes = [item[:100] for item in values[:8] if isinstance(item, str)]
                    limits.append(label + "：" + "、".join(codes))
        limits.append("维护结论限于原任务复查范围，不提升市场数据或研究证据资格。")
    else:
        return None
    # Source references are identifiers, never a model/provider supplied URL.
    if "id" in source and (type(source["id"]) is not int or source["id"] <= 0):
        return None
    return {
        "source": source,
        "observed_at": datetime.now(UTC).isoformat(),
        "facts": facts,
        "limitations": limits,
        "unavailable_sections": [
            key[:-6] for key, item in value.items() if key.endswith("_error") and item
        ][:8],
    }
