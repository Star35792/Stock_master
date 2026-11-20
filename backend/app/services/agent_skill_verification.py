"""Bounded read/observe/verify loop; the model never supplies evidence or verdicts."""

import json
import re

from pydantic import Field

from app.schemas.agent_skills import AgentSkillVerification
from app.services.agent_result_evidence import result_evidence
from app.services.agent_skills import SkillArgs, SkillContext

REQUIRED_READS = {
    "data-quality-diagnosis": ("operations_overview", "quality_issues"),
    "factor-backtest-review": ("backtest_result",),
    "position-risk-review": ("position_holdings",),
}


class VerifySkillArgs(SkillArgs):
    resource_id: int | None = Field(default=None, gt=0)


def verification_tool_definition():
    return {
        "type": "function",
        "function": {
            "name": "verify_skill",
            "description": "按本轮实际只读结果检查材料，返回缺失项供补查；不能认证研究或授权。",
            "parameters": VerifySkillArgs.model_json_schema(),
        },
    }


def observation(name, args, value):
    """Extract bounded original-service evidence before the chat display truncates results."""
    if not isinstance(value, dict) or any(
        value.get(key) for key in ("error", "blocked", "unavailable", "truncated")
    ):
        return None
    evidence = result_evidence(value)
    if name == "backtest_result":
        run = value.get("run") or {}
        if not evidence or run.get("id") != args.get("run_id") or run.get("status") != "success":
            return None
        for key in ("start_date", "end_date", "code_version", "data_version"):
            if not run.get(key):
                return None
        missing = []
        performance = value.get("performance")
        if not isinstance(performance, dict) or performance.get("total_return") is None:
            missing.append("原绩效指标")
        contract = run.get("parameters") or {}
        fee_model = run.get("fee_model_version") or contract.get("fee_model_version")
        if not fee_model:
            missing.append("原费用合同")
        return {
            "tool": name,
            "resource_id": args["run_id"],
            "evidence": evidence,
            "missing_checks": missing,
            "fee_model_version": fee_model,
        }
    if name == "position_holdings":
        if not evidence or value.get("book_id") != args.get("book_id"):
            return None
        # Preserve original risk diagnostics, never derive a second financial calculation.
        fields = ("concentration", "industry_exposure")
        risk = {key: value.get(key) for key in fields}
        raw = json.dumps(risk, ensure_ascii=False)
        limits = []
        if any(value.get(key) is None for key in fields):
            limits.append("原服务未提供完整集中度或行业覆盖。")
        if len(raw.encode()) > 4000:
            risk = {"unavailable": "原风险诊断超过摘要上限，请打开持仓详情。"}
            limits.append("集中度或行业诊断过大，不能宣称已完整解读。")
        return {
            "tool": name,
            "resource_id": args["book_id"],
            "evidence": evidence,
            "risk_diagnostics": risk,
            "limitations": limits,
            "missing_checks": [key for key in fields if value.get(key) is None]
            + (["风险诊断超过摘要上限"] if "unavailable" in risk else []),
        }
    if name == "operations_overview":
        quality = value.get("data_quality")
        if not value.get("generated_at") or not isinstance(quality, dict):
            return None
        counts = {}
        for key in ("issue_status_counts", "open_issue_severity_counts"):
            mapping = quality.get(key)
            if (
                not isinstance(mapping, dict)
                or len(mapping) > 16
                or any(
                    not isinstance(k, str) or len(k) > 80 or type(v) is not int or v < 0
                    for k, v in mapping.items()
                )
            ):
                return None
            counts[key] = mapping
        return {"tool": name, "generated_at": value["generated_at"], **counts}
    if name == "quality_issues":
        if any(
            type(value.get(key)) is not int or value[key] < 0
            for key in ("total", "limit", "offset")
        ):
            return None
        items = value.get("items")
        if not isinstance(items, list) or len(items) > args.get("limit", 10):
            return None
        return {
            "tool": name,
            "status_scope": "open",
            "symbol": args.get("symbol"),
            "total": value["total"],
            "limit": value["limit"],
            "offset": value["offset"],
            "returned": len(items),
            "issue_ids": [row["id"] for row in items if type(row.get("id")) is int],
        }
    return None


class SkillTurn(SkillContext):
    def __init__(self, registry, message, user_messages, clock):
        super().__init__(registry, message)
        self.user_text = "\n".join(user_messages)
        self.clock = clock
        self.observations = {}
        self.read_cache = {}
        self.reports = {}
        self.check_calls = 0
        self.scopes = {}

    def read_key(self, name, args):
        if not self.registry.matches(self.identities):
            raise ValueError("技能版本已变化，请重新发起需求。")
        if self.loaded:
            for key in ("run_id", "book_id", "symbol"):
                if args.get(key) is not None and not re.search(
                    r"(?<![a-zA-Z0-9_.])" + re.escape(str(args[key])) + r"(?![a-zA-Z0-9_.])",
                    self.user_text,
                ):
                    raise ValueError("请由用户明确证券或资源ID，技能不能提供参数事实。")
        return name, json.dumps(args, sort_keys=True)

    def observe(self, name, args, value):
        if not self.loaded:
            return  # A later Skill load cannot retroactively bless unscoped reads.
        self.read_key(name, args)
        self.scopes.setdefault(name, set()).add(
            args.get("run_id", args.get("book_id", args.get("symbol")))
        )
        proof = observation(name, args, value)
        key = (name, args.get("run_id", args.get("book_id")))
        if proof:
            proof["observed_at"] = self.clock()
            self.observations[key] = proof
        else:
            self.observations.pop(key, None)
        self.reports.clear()  # Every new observation needs a new check.

    def verify(self, arguments, *, counted=True):
        args = VerifySkillArgs.model_validate(arguments)
        if counted and self.check_calls >= 4:
            raise ValueError("本轮核验次数达到4次上限。")
        self.check_calls += int(counted)
        package = self.loaded.get(args.name)
        if package is None or not self.registry.matches([package.identity]):
            raise ValueError("请先加载当前技能版本。")
        required = REQUIRED_READS.get(args.name, ())
        resource = args.resource_id
        needs_id = args.name in {"factor-backtest-review", "position-risk-review"}
        missing, refs = [], []
        for name in required:
            proof = self.observations.get((name, resource if needs_id else None))
            if proof:
                refs.append(proof)
                missing.extend(proof.get("missing_checks", []))
            else:
                missing.append(name)
            if len(self.scopes.get(name, ())) > 1:
                missing.append("首版每技能仅核查单一资源，请分别提交多资源需求。")
        if not required:
            missing.append("该技能尚未注册服务端检查合同，只能提供方法指导。")
        limits = ["材料检查不代表研究目标、策略资格或持仓安全已认证。"]
        if args.name == "data-quality-diagnosis":
            limits.append("问题列表仅open；概览严重度含open/reopened，最近样本可含其他状态。")
            limits.append("仅诊断已返回范围；样本为空或材料齐备不代表全市场数据无问题。")
        for ref in refs:
            limits.extend(ref.get("evidence", {}).get("limitations", []))
            limits.extend(ref.get("limitations", []))
        report = AgentSkillVerification(
            skill=package.identity,
            status="needs_input"
            if needs_id and resource is None
            else "needs_evidence"
            if missing
            else "ready_to_explain",
            task_check_complete=not missing and (not needs_id or resource is not None),
            observed_at=max(
                (ref.get("observed_at", self.clock()) for ref in refs), default=self.clock()
            ),
            missing_checks=missing,
            evidence_refs=refs,
            limitations=list(dict.fromkeys(limits))[:12],
        )
        self.reports[args.name] = report
        return report

    def finish(self):
        for name in self.loaded:
            if name not in self.reports:
                required = REQUIRED_READS.get(name, ())
                resources = {resource for tool, resource in self.observations if tool in required}
                resource = next(iter(resources)) if len(resources) == 1 else None
                self.verify({"name": name, "resource_id": resource}, counted=False)
        return list(self.reports.values())
