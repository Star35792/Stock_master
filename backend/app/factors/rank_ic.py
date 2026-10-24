"""Shared cross-sectional IC mathematics.

Single source of truth for Pearson correlation, average-rank percentiles and
IC / RankIC used by both ``FactorCrossSectionService`` (factor profiles) and
``TrainingFactorSelector`` (per-window training selection).  All arithmetic
uses ``Decimal`` with precision 28 so that persisted values and artifact
snapshot hashes stay exactly reproducible; do not add a parallel float path.
"""

from decimal import Decimal, localcontext


def mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal()) / Decimal(len(values))


def standard_deviation(values: list[Decimal]) -> Decimal | None:
    avg = mean(values)
    if avg is None:
        return None
    variance = sum(((value - avg) ** 2 for value in values), Decimal()) / Decimal(len(values))
    with localcontext() as context:
        context.prec = 28
        return variance.sqrt()


def correlation(left: list[Decimal], right: list[Decimal]) -> Decimal | None:
    left_mean = mean(left)
    right_mean = mean(right)
    if left_mean is None or right_mean is None:
        return None
    covariance = sum(
        ((left_value - left_mean) * (right_value - right_mean))
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_sum_squares = sum(((value - left_mean) ** 2 for value in left), Decimal())
    right_sum_squares = sum(((value - right_mean) ** 2 for value in right), Decimal())
    if not left_sum_squares or not right_sum_squares:
        return None
    with localcontext() as context:
        context.prec = 28
        denominator = (left_sum_squares * right_sum_squares).sqrt()
        return covariance / denominator


def rank_values(values: dict[str, Decimal]) -> dict[str, Decimal]:
    """Average-rank percentiles in [0, 1]; ties share the average rank."""
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    count = len(ordered)
    if count == 0:
        return {}
    if count == 1:
        return {ordered[0][0]: Decimal("0.5")}
    ranks: dict[str, Decimal] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average_rank = Decimal(index + end) / Decimal(2)
        percentile = average_rank / Decimal(count - 1)
        for position in range(index, end + 1):
            ranks[ordered[position][0]] = percentile
        index = end + 1
    return ranks


def information_coefficient(
    factor_values: dict[str, Decimal],
    forward_returns: dict[str, Decimal],
) -> Decimal | None:
    common_symbols = sorted(set(factor_values) & set(forward_returns))
    if len(common_symbols) < 2:
        return None
    return correlation(
        [factor_values[symbol] for symbol in common_symbols],
        [forward_returns[symbol] for symbol in common_symbols],
    )


def rank_information_coefficient(
    factor_values: dict[str, Decimal],
    forward_returns: dict[str, Decimal],
) -> Decimal | None:
    common_symbols = sorted(set(factor_values) & set(forward_returns))
    if len(common_symbols) < 2:
        return None
    factor_ranks = rank_values({symbol: factor_values[symbol] for symbol in common_symbols})
    return_ranks = rank_values({symbol: forward_returns[symbol] for symbol in common_symbols})
    return correlation(
        [factor_ranks[symbol] for symbol in common_symbols],
        [return_ranks[symbol] for symbol in common_symbols],
    )
