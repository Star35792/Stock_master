from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.research_base import ResearchBase
from app.models.common import CreatedAtMixin


class ExternalObservation(CreatedAtMixin, ResearchBase):
    """One external observation of a security, captured from a third party.

    The built-in value-investing screen runs on the project's own point-in-time
    data, and it must keep doing so: its gates have to be reproducible from a
    dated snapshot, and the security universe has to be the one that existed on
    the screen date.  External sources -- a vendor's financial indicators, a
    sell-side consensus estimate, an analyst's market-share note -- cannot
    satisfy that.  They are dated by *when they were captured*, not by when
    the underlying information became public, and consensus estimates are
    forward-looking by construction.

    So these observations are stored, never gated on.  They exist so a human
    reading the research card can see what the outside world says about the
    shortlist, and so that the capture is itself reproducible and auditable
    rather than pasted into a note.

    The table lives in the standalone research database: it holds research
    artifacts, not control-plane facts.
    """

    __tablename__ = "external_observation"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "source",
            "observation_type",
            "as_of_date",
            name="uq_external_observation_identity",
        ),
        Index("ix_external_observation_symbol", "symbol"),
        Index("ix_external_observation_source", "source"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("strategy_external_observation_id_seq"),
        server_default=text("nextval('strategy_external_observation_id_seq')"),
        primary_key=True,
    )
    #: Security the observation is about.
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Which external system produced it, e.g. ``neodata`` or ``westock``.
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    #: What kind of observation it is, e.g. ``company_profile``.
    observation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    #: Date the observation refers to.  For a consensus estimate this is the
    #: forecast year, not the capture date; the capture date is
    #: ``captured_at``.  Keeping both means a reader can tell how stale an
    #: observation is.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: When the observation was captured.
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: The observation itself, verbatim as returned by the source.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: Schema version of the payload, so a later reader can tell whether it
    #: knows the shape it is looking at.
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
