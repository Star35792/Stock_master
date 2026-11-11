"""External research card for the value-investing shortlist.

The screen itself runs on the project's own point-in-time data and its
gates never read this module.  A *research card* is the second half of the
job: it attaches what the outside world says about the shortlisted
securities -- what the business does, what the sell side expects, whether
any analyst covers it -- so a human can decide whether the numbers on the
screen deserve a closer look.

Three rules govern this module, and they exist because breaking any of them
would make the card look more authoritative than it is:

1. **Nothing here can change a gate.**  The card is assembled from the
   screen's output plus external observations.  It has no path back into
   the kernel.
2. **Forward-looking data is labelled as such.**  A consensus estimate for
   2028 is a forecast, not a fact; it is reported under ``forward_looking``
   and never mixed into the point-in-time evidence.
3. **An absent observation is an absence, not a zero.**  If no analyst
   covers a security, the card says so.  It does not manufacture a zero
   forecast or infer an absence of risk from missing coverage.

The card lives in the standalone research database because it is a research
artifact, not a control-plane fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import ExternalObservation

#: Observation types this module understands.  Anything else is stored but
#: not rendered, so a new source cannot silently change the card's shape.
OBSERVATION_TYPES = (
    "company_profile",
    "financial_indicators",
    "consensus",
    "rating",
    "research_report",
)

#: Sources this module is willing to read.
KNOWN_SOURCES = ("neodata", "westock")


@dataclass(frozen=True)
class ResearchCard:
    """What is known about one security from outside the project."""

    symbol: str
    name: str | None = None
    #: Free-text description of what the company does.  This is the answer
    #: to "how does the business earn money" -- the one dimension the
    #: project's own data could not answer.
    business: str | None = None
    #: Shenwan industry classification, when the source provides one.
    industry_path: str | None = None
    #: Employee count, a crude scale proxy.
    employee_count: int | None = None
    #: Nature of the controlling shareholder: private, local state-owned,
    #: central state-owned.  Relevant to governance questions the project
    #: cannot otherwise research.
    enterprise_type: str | None = None
    #: Consensus EPS by forecast year.  **Forward looking.**
    consensus_eps: dict[int, Decimal] = field(default_factory=dict)
    #: Consensus net profit by forecast year.  **Forward looking.**
    consensus_net_profit: dict[int, Decimal] = field(default_factory=dict)
    #: Consensus revenue by forecast year.  **Forward looking.**
    consensus_revenue: dict[int, Decimal] = field(default_factory=dict)
    #: Consensus PE by forecast year.  **Forward looking.**
    consensus_pe: dict[int, Decimal] = field(default_factory=dict)
    #: Consensus target price.
    target_price: Decimal | None = None
    #: How many institutions publish a rating, and how many rate it a buy.
    rating_count: int | None = None
    rating_buy_count: int | None = None
    #: Titles of sell-side research notes, newest first.
    research_titles: tuple[str, ...] = ()
    #: Independent vendor indicators, keyed by name.
    vendor_indicators: dict[str, Decimal] = field(default_factory=dict)
    #: The observations this card was built from, for provenance.
    observation_refs: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "business": self.business,
            "industry_path": self.industry_path,
            "employee_count": self.employee_count,
            "enterprise_type": self.enterprise_type,
            "forward_looking": {
                "consensus_eps": {
                    str(year): str(value)
                    for year, value in sorted(self.consensus_eps.items())
                },
                "consensus_net_profit": {
                    str(year): str(value)
                    for year, value in sorted(self.consensus_net_profit.items())
                },
                "consensus_revenue": {
                    str(year): str(value)
                    for year, value in sorted(self.consensus_revenue.items())
                },
                "consensus_pe": {
                    str(year): str(value)
                    for year, value in sorted(self.consensus_pe.items())
                },
                "target_price": (
                    str(self.target_price) if self.target_price is not None else None
                ),
                "warning": (
                    "机构一致预期是前瞻性预测，不是已实现事实，"
                    "不参与本策略的任何闸门判定。"
                ),
            },
            "rating": {
                "rating_count": self.rating_count,
                "rating_buy_count": self.rating_buy_count,
                "note": (
                    "覆盖机构少不代表风险小，只代表市场关注度低。"
                    if self.rating_count is not None and self.rating_count <= 1
                    else None
                ),
            },
            "research_titles": list(self.research_titles),
            "vendor_indicators": {
                key: str(value) for key, value in sorted(self.vendor_indicators.items())
            },
            "observation_refs": list(self.observation_refs),
        }


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed vendor value is just absent
        return None


def _sw_industry_path(raw: Any) -> str | None:
    """Flatten the Shenwan classification blob into ``一级 > 二级 > 三级``."""
    if not raw:
        return None
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, list):
        return None
    levels: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        plate_type = str(entry.get("PlateType") or "")
        plate_name = str(entry.get("PlateName") or "")
        if "申万一级" in plate_type:
            levels["1"] = plate_name
        elif "申万二级" in plate_type:
            levels["2"] = plate_name
        elif "申万三级" in plate_type:
            levels["3"] = plate_name
    ordered = [levels[key] for key in ("1", "2", "3") if levels.get(key)]
    return " > ".join(ordered) if ordered else None


@dataclass
class _CardState:
    """Mutable working state while the card is assembled.

    The finished card is immutable; this is just the scratch space the
    appliers write into.
    """

    business: str | None = None
    industry_path: str | None = None
    employee_count: int | None = None
    enterprise_type: str | None = None
    consensus_eps: dict[int, Decimal] = field(default_factory=dict)
    consensus_net_profit: dict[int, Decimal] = field(default_factory=dict)
    consensus_revenue: dict[int, Decimal] = field(default_factory=dict)
    consensus_pe: dict[int, Decimal] = field(default_factory=dict)
    target_price: Decimal | None = None
    rating_count: int | None = None
    rating_buy_count: int | None = None
    research_titles: list[str] = field(default_factory=list)
    vendor_indicators: dict[str, Decimal] = field(default_factory=dict)
    observation_refs: list[dict[str, Any]] = field(default_factory=list)


def _apply_company_profile(state: _CardState, payload: dict[str, Any]) -> None:
    business = payload.get("business")
    if isinstance(business, str) and business.strip():
        state.business = business.strip()
    path = _sw_industry_path(payload.get("sw_info"))
    if path:
        state.industry_path = path
    employees = payload.get("employee_sum")
    if employees is not None:
        try:
            state.employee_count = int(employees)
        except (TypeError, ValueError):
            pass
    enterprise_type = payload.get("enterprise_type")
    if isinstance(enterprise_type, str) and enterprise_type.strip():
        state.enterprise_type = enterprise_type.strip()


def _apply_consensus(state: _CardState, payload: dict[str, Any]) -> None:
    target = _decimal(payload.get("targetPrice"))
    if target is not None and target > 0:
        state.target_price = target
    for forecast in payload.get("forecasts") or []:
        if not isinstance(forecast, dict):
            continue
        year = forecast.get("year")
        if not isinstance(year, int):
            continue
        eps = _decimal(forecast.get("eps"))
        if eps is not None:
            state.consensus_eps[year] = eps
        net_profit = _decimal(forecast.get("netProfit"))
        if net_profit is not None:
            state.consensus_net_profit[year] = net_profit
        revenue = _decimal(forecast.get("revenue"))
        if revenue is not None:
            state.consensus_revenue[year] = revenue
        pe = _decimal(forecast.get("pe"))
        if pe is not None:
            state.consensus_pe[year] = pe


def _apply_rating(state: _CardState, payload: dict[str, Any]) -> None:
    count = payload.get("ratingCnt")
    if isinstance(count, int):
        state.rating_count = count
    buy = payload.get("ratingBuyCnt")
    if isinstance(buy, int):
        state.rating_buy_count = buy


def _apply_research_report(state: _CardState, payload: dict[str, Any]) -> None:
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        state.research_titles.append(title.strip())


def _apply_financial_indicators(
    state: _CardState, payload: dict[str, Any]
) -> None:
    for key, value in payload.items():
        if key in {"stock_code", "stock_name", "fiscal_period", "end_date", "currency"}:
            continue
        number = _decimal(value)
        if number is not None:
            state.vendor_indicators[key] = number


_APPLIERS = {
    "company_profile": _apply_company_profile,
    "consensus": _apply_consensus,
    "rating": _apply_rating,
    "research_report": _apply_research_report,
    "financial_indicators": _apply_financial_indicators,
}


def build_card(
    symbol: str,
    observations: list[ExternalObservation],
    *,
    name: str | None = None,
) -> ResearchCard:
    """Assemble the research card for one security.

    Observations are applied in capture order, so a later capture of the
    same kind wins.  That is the right precedence for a vendor feed: the
    most recent snapshot is the most current one.
    """
    state = _CardState()
    ordered = sorted(observations, key=lambda item: item.captured_at)
    for observation in ordered:
        applier = _APPLIERS.get(observation.observation_type)
        payload = observation.payload
        if applier is None or not isinstance(payload, dict):
            continue
        applier(state, payload)
        state.observation_refs.append(
            {
                "source": observation.source,
                "observation_type": observation.observation_type,
                "as_of_date": observation.as_of_date.isoformat(),
                "captured_at": observation.captured_at.isoformat(),
            }
        )
    return ResearchCard(
        symbol=symbol,
        name=name,
        business=state.business,
        industry_path=state.industry_path,
        employee_count=state.employee_count,
        enterprise_type=state.enterprise_type,
        consensus_eps=state.consensus_eps,
        consensus_net_profit=state.consensus_net_profit,
        consensus_revenue=state.consensus_revenue,
        consensus_pe=state.consensus_pe,
        target_price=state.target_price,
        rating_count=state.rating_count,
        rating_buy_count=state.rating_buy_count,
        research_titles=tuple(state.research_titles),
        vendor_indicators=state.vendor_indicators,
        observation_refs=tuple(state.observation_refs),
    )


def build_cards(
    db: Session,
    symbols: list[str],
    *,
    names: dict[str, str] | None = None,
    source: str | None = None,
) -> list[ResearchCard]:
    """Build the research cards for a set of securities.

    A security with no observation produces no card.  An empty card would
    read as "we looked and found nothing", which is different from "we never
    looked" -- and the difference matters when the card is being used to
    check whether the screen's rejections are justified.
    """
    if not symbols:
        return []
    statement = select(ExternalObservation).where(
        ExternalObservation.symbol.in_(symbols)
    )
    if source is not None:
        statement = statement.where(ExternalObservation.source == source)
    rows = list(db.execute(statement).scalars())
    grouped: dict[str, list[ExternalObservation]] = {}
    for row in rows:
        grouped.setdefault(row.symbol, []).append(row)
    return [
        build_card(
            symbol,
            grouped[symbol],
            name=(names or {}).get(symbol),
        )
        for symbol in symbols
        if symbol in grouped
    ]


def card_to_markdown(card: ResearchCard) -> str:
    """Render one research card as Markdown."""
    lines = [f"### {card.symbol} {card.name or ''}".rstrip()]
    lines.append("")
    if card.business:
        lines.append(f"- **主营业务**：{card.business}")
    if card.industry_path:
        lines.append(f"- **申万行业**：{card.industry_path}")
    if card.enterprise_type:
        lines.append(f"- **企业性质**：{card.enterprise_type}")
    if card.employee_count is not None:
        lines.append(f"- **员工人数**：{card.employee_count:,}")
    if card.rating_count is not None:
        buy = card.rating_buy_count if card.rating_buy_count is not None else 0
        lines.append(f"- **机构评级**：{buy}/{card.rating_count} 买入")
    if card.target_price is not None:
        lines.append(f"- **一致目标价**：{card.target_price}")
    if card.consensus_eps:
        years = sorted(card.consensus_eps)
        detail = "、".join(
            f"{year}E EPS {card.consensus_eps[year]}" for year in years
        )
        lines.append(f"- **一致预期**：{detail}")
    if card.consensus_pe:
        detail = "、".join(
            f"{year}E PE {card.consensus_pe[year]}" for year in sorted(card.consensus_pe)
        )
        lines.append(f"- **一致预期估值**：{detail}")
    if card.research_titles:
        lines.append("- **卖方研报**：")
        for title in card.research_titles:
            lines.append(f"  - {title}")
    if card.vendor_indicators:
        detail = "、".join(
            f"{key} {value}"
            for key, value in sorted(card.vendor_indicators.items())
        )
        lines.append(f"- **外部财务指标**：{detail}")
    if len(lines) == 2:
        lines.append("- 暂无外部观测数据。")
    lines.append("")
    return "\n".join(lines)


def capture_date_of(observation: ExternalObservation) -> date:
    """The date the observation refers to (not the capture date)."""
    return observation.as_of_date


def observation_payloads(
    rows: list[ExternalObservation],
) -> list[dict[str, Any]]:
    """Flatten observations for storage or for review."""
    return [
        {
            "symbol": row.symbol,
            "source": row.source,
            "observation_type": row.observation_type,
            "as_of_date": row.as_of_date.isoformat(),
            "captured_at": row.captured_at.isoformat(),
            "payload": row.payload,
        }
        for row in rows
    ]
