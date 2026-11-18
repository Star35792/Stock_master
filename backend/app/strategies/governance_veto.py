"""Manual governance vetoes applied to the value-investing shortlist.

The quantitative gates in :mod:`app.strategies.value_investing` only see the
project's own point-in-time data.  A *governance veto* is a human research
judgement that a security which passes those gates should nevertheless not
be promoted to the research shortlist, because of a management / governance
event the project has no data source for.  In ``quality_profile`` the
``management_integrity`` dimension is a weak proxy at best -- five-year
dividend continuity cannot see a management scandal -- so the only honest
way to reflect such a judgement is an explicit, audited veto.

Three rules keep this honest:

1. **A veto never changes a gate.**  The kernel still reports the security
   as ``qualified``; the veto is applied at the report layer and recorded
   separately, so "passed every quantitative gate" and "human rejected it"
   are both visible and neither is silently overwritten.
2. **Every veto carries evidence** -- the event, the date it became known,
   and the public sources -- so the decision can be audited, disputed, and
   reversed.
3. **A veto is a *current* research judgement, not a historical fact.**  It
   is never back-filled into historical screens, so it cannot create
   look-ahead bias in a backtest.  The ``vetoed_at`` date records when the
   human made the call, not when the underlying event happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class GovernanceVeto:
    """One human governance judgement against a security."""

    symbol: str
    name: str
    #: One-line reason, e.g. "管理层诚信红旗：应届生批量解约并被公开追责".
    reason: str
    #: What actually happened, in enough detail to audit the call later.
    event: str
    #: Public sources / evidence URLs.
    evidence: tuple[str, ...]
    #: The date this veto was entered, not the date of the event.
    vetoed_at: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "reason": self.reason,
            "event": self.event,
            "evidence": list(self.evidence),
            "vetoed_at": self.vetoed_at.isoformat(),
        }


#: The audited list of governance vetoes.  Add a row when a human decides a
#: passing security should not be promoted; keep the evidence so the decision
#: can be revisited.  This is the single place where a veto is declared.
GOVERNANCE_VETOES: tuple[GovernanceVeto, ...] = (
    GovernanceVeto(
        symbol="601799.SH",
        name="星宇股份",
        reason="管理层诚信红旗：2026 应届生批量解约，实控人兼总经理被公开追责",
        event=(
            "2026-07 招录 440 名 2026 届应届生，2026-08 对其中 107 名粗暴劝退"
            "（被曝 HR 放话「不辞职就去流水线拧螺丝」「配合签字背调不为难」），"
            "引发舆论与监管介入：常州市人社局通报「方法简单生硬」、大众中国启动"
            "专项调查、奔驰立案、港交所收到投诉转上市科核查（正值其冲刺港股 IPO）。"
            "2026-09-07 公司发文追责：实控人、董事长兼总经理周晓萍扣薪 12 个月，"
            "副总经理李树军调岗并扣薪 6 个月，人力资源总监俞志明免职。"
        ),
        evidence=(
            "https://m.21jingji.com/article/20260907/herald/76937ab472171f9cd3e9d9ceb63d3de8.html",
            "https://www.sina.cn/news/detail/5340514075546071.html",
        ),
        vetoed_at=date(2026, 11, 16),
    ),
)


def veto_by_symbol() -> dict[str, GovernanceVeto]:
    """Return ``symbol -> veto`` for constant-time lookups in the report layer."""
    return {veto.symbol: veto for veto in GOVERNANCE_VETOES}


def apply_governance_vetoes(result: dict[str, Any], *, as_of_date: date) -> dict[str, Any]:
    """Apply governance vetoes to a screening result at the report layer.

    The kernel result is left intact -- ``qualified_count`` still counts every
    security that passed the quantitative gates -- and the veto is recorded as
    a separate, audited overlay:

    * ``governance_vetoes`` lists every vetoed security that appeared in the
      shortlist or deferred pool, with the veto record plus the security's own
      row for context.
    * ``active_shortlist`` / ``active_deferred`` are the pools with the vetoed
      names removed, i.e. what a human should actually research.
    * ``vetoed_count`` counts the removed names.

    Returns a new dict; the input is not mutated.
    """
    # A historical screen may only consume decisions already made on its
    # research date. The current screen passes today's date separately from
    # the last completed price date.
    vetoes = {
        symbol: veto
        for symbol, veto in veto_by_symbol().items()
        if veto.vetoed_at <= as_of_date
    }
    def _split(pool: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        kept: list[dict[str, Any]] = []
        vetoed: list[dict[str, Any]] = []
        for row in pool:
            veto = vetoes.get(row.get("symbol") or "")
            if veto is None:
                kept.append(row)
            else:
                vetoed.append({**row, "governance_veto": veto.to_dict()})
        return kept, vetoed

    active_shortlist, vetoed_shortlist = _split(result.get("shortlist", []))
    active_deferred, vetoed_deferred = _split(result.get("deferred", []))
    vetoed = vetoed_shortlist + vetoed_deferred

    return {
        **result,
        "governance_vetoes": vetoed,
        "vetoed_count": len(vetoed),
        "active_shortlist": active_shortlist,
        "active_deferred": active_deferred,
        "active_shortlist_count": len(active_shortlist),
        "active_deferred_count": len(active_deferred),
    }
