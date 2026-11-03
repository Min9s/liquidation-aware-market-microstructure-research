"""Potential liquidation exposure, not liquidation probability or a trade signal."""

import math
from collections.abc import Iterable


def _number(value: float | str, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def gravity(exposures: Iterable[tuple[float, float]], tau_bps: float) -> float:
    """Sum notional * exp(-distance_bps / tau_bps), without a distance cutoff.

    Notional and distance must be finite and nonnegative; tau must be positive.
    Empty observed exposure returns 0. Missing observations are not empty input.
    """
    tau = _number(tau_bps, "tau_bps")
    if tau <= 0:
        raise ValueError("tau_bps must be positive")
    terms = []
    for exposure in exposures:
        if not isinstance(exposure, (tuple, list)) or len(exposure) != 2:
            raise ValueError("Each exposure must be a (notional, distance_bps) pair")
        raw_notional, raw_distance = exposure
        notional = _number(raw_notional, "notional")
        distance = _number(raw_distance, "distance_bps")
        if notional < 0 or distance < 0:
            raise ValueError("Notional and distance must be nonnegative")
        terms.append(notional * math.exp(-distance / tau))
    try:
        total = math.fsum(terms)
    except OverflowError as error:
        raise ValueError("Gravity exceeds the finite float range") from error
    if not math.isfinite(total):
        raise ValueError("Gravity exceeds the finite float range")
    return total


def liquidation_imbalance(up: float, down: float, epsilon: float = 1e-12) -> float:
    """LGI = (up - down) / (up + down + epsilon); observed empty input is 0."""
    up = _number(up, "up")
    down = _number(down, "down")
    epsilon = _number(epsilon, "epsilon")
    if up < 0 or down < 0 or epsilon <= 0:
        raise ValueError("Gravity must be nonnegative and epsilon positive")
    denominator = up + down + epsilon
    if not math.isfinite(denominator):
        raise ValueError("LGI denominator exceeds the finite float range")
    return (up - down) / denominator


def prepare_exposure(
    signed_size: float | str,
    liquidation_price: float | str | None,
    reference_price: float | str,
) -> dict[str, object]:
    """Prepare ONE position with an explicit inclusion/exclusion reason.

    Long exposure belongs below reference (lower); short exposure above (upper).
    Missing/invalid thresholds are excluded, never inferred or replaced by zero.
    This function does not select snapshots, aggregate a surface, or infer fills.
    """
    size = _number(signed_size, "signed_size")
    reference = _number(reference_price, "reference_price")
    if reference <= 0:
        raise ValueError("reference_price must be positive")
    notional = abs(size) * reference
    if not math.isfinite(notional):
        raise ValueError("Position notional exceeds the finite float range")
    result = {
        "status": "excluded",
        "reason": "zero_position",
        "side": "lower" if size > 0 else "upper" if size < 0 else None,
        "notional": notional,
        "liquidation_price": None,
        "distance_bps": None,
    }
    if size == 0:
        return result
    if liquidation_price is None:
        result["reason"] = "missing_liquidation_price"
        return result
    try:
        threshold = _number(liquidation_price, "liquidation_price")
    except ValueError:
        result["reason"] = "invalid_liquidation_price"
        return result
    if threshold <= 0:
        result["reason"] = "invalid_liquidation_price"
        return result
    result["liquidation_price"] = threshold
    if threshold == reference:
        result["reason"] = "at_reference_price"
        return result
    if (size > 0 and threshold > reference) or (size < 0 and threshold < reference):
        result["reason"] = "wrong_side_of_reference"
        return result
    distance = abs(threshold - reference) / reference * 10_000
    if not math.isfinite(distance):
        raise ValueError("Liquidation distance exceeds the finite float range")
    result.update(status="included", reason="included", distance_bps=distance)
    return result
