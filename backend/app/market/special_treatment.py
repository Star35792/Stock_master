"""Derive the special-treatment (ST / *ST) status of a security.

Why this module exists
----------------------
``market.daily_bar.special_treatment_status`` is read by the price-limit
derivation, by the screening service, and by the paper execution path, but
no data source ever wrote it.  The field therefore stayed NULL for 99.4% of
the rows, and because the screen treats "unknown" as "restricted", every
candidate was rejected: the nightly screen produced zero reviewable
candidates across 83 consecutive runs.

The status is derived from the security's *name*, which the exchanges change
when a stock enters or leaves special treatment.  A stock under special
treatment carries an ``ST`` / ``*ST`` / ``S*ST`` / ``SST`` marker in its
short name, so the name is direct evidence for the current status.

Scope limits
------------
* This yields the **current** status, not a dated history.  A name-based
  status cannot be back-filled onto historical bars: the supplier serves the
  current name on every historical row, so applying it backwards would
  create a look-ahead instead of filling a gap.
* A name that ends with the delisting marker (``退``) is reported as
  ``delisting`` rather than ``ST``: those securities are in the delisting
  consolidation period and are handled by the tradability policy, not by the
  price band.
* The result is ``None`` when the name carries no marker at all.  ``None``
  means "not special treated" for the screen, but it must never be
  interpreted as "verified as normal" for historical bars.
"""

from __future__ import annotations

import re

#: Statuses reported for a security under special treatment.
ST_STATUS = "ST"

#: Statuses reported for a security in the delisting consolidation period.
DELISTING_STATUS = "delisting"

#: Statuses reported for a security whose name carries no special marker.
NORMAL_STATUS = "normal"

#: Markers the exchanges prepend to a short name.  ``S*ST`` and ``SST`` are
#: historical mainland markers that still appear in vendor name lists.
_ST_MARKERS: tuple[str, ...] = ("*ST", "S*ST", "SST", "ST")

#: A trailing ``退`` marks the delisting consolidation period.
_DELISTING_MARKER = "退"

#: Names such as ``*ST海王`` carry the marker at the front.  Some vendors
#: prefix the name with an exchange or sector tag, so the marker is searched
#: in the leading token rather than at position zero.
_TOKEN_SPLIT = re.compile(r"[\s\-_/]+")


def _leading_token(name: str) -> str:
    """Return the leading token of a short name."""
    tokens = _TOKEN_SPLIT.split(name.strip())
    return tokens[0] if tokens else ""


def derive_special_treatment_status(name: str | None) -> str | None:
    """Return the special-treatment status implied by a security short name.

    ``None`` is returned when the name carries no marker.  Callers that need
    to distinguish "not special treated" from "unknown" must resolve the
    status against the security master first, because a missing name is not
    evidence of a normal security.
    """
    if not name:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None
    token = _leading_token(cleaned)
    for marker in _ST_MARKERS:
        if token.startswith(marker) or token == marker:
            # The delisting marker takes precedence: a security that is both
            # ST and in the delisting period is handled by the delisting
            # branch, not by the price band.
            if _DELISTING_MARKER in token:
                return DELISTING_STATUS
            return ST_STATUS
    if _DELISTING_MARKER in token:
        return DELISTING_STATUS
    return NORMAL_STATUS


def is_special_treatment(name: str | None) -> bool:
    """Return whether a short name marks the security as ST / *ST."""
    return derive_special_treatment_status(name) == ST_STATUS


__all__ = [
    "DELISTING_STATUS",
    "NORMAL_STATUS",
    "ST_STATUS",
    "derive_special_treatment_status",
    "is_special_treatment",
]
