from enum import StrEnum


class ConstraintEnforcementMode(StrEnum):
    """How a group budget violation is resolved."""

    PROPORTIONAL_SCALE = "proportional_scale"
    SCORE_RANKED_TRIM = "score_ranked_trim"
