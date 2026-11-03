"""Best-level quote metrics. Prices and quantities must use consistent units."""

import math


def _number(value: float | str, name: str) -> float:
    """Convert a finite, nonnegative number or numeric string to float."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite nonnegative number") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return number


def _prices(bid_price: float | str, ask_price: float | str) -> tuple[float, float]:
    bid = _number(bid_price, "bid_price")
    ask = _number(ask_price, "ask_price")
    if bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("Prices must satisfy 0 < bid_price <= ask_price")
    return bid, ask


def bid_ask_spread(bid_price: float | str, ask_price: float | str) -> dict[str, float]:
    """Return mid, absolute spread and spread in basis points (1 bp = 0.01%)."""
    bid, ask = _prices(bid_price, ask_price)
    absolute = ask - bid
    # This equivalent midpoint avoids overflowing bid + ask for large prices.
    mid = bid + absolute / 2
    return {"mid": mid, "absolute": absolute, "bps": absolute / mid * 10_000}


def order_book_imbalance(bid_size: float | str, ask_size: float | str) -> float | None:
    """Return (bid_size - ask_size) / total size, or None for zero total size."""
    bid = _number(bid_size, "bid_size")
    ask = _number(ask_size, "ask_size")
    total = bid + ask
    if not math.isfinite(total):
        raise ValueError("Total size exceeds the finite float range")
    return (bid - ask) / total if total else None


def notional_depth(
    bid_price: float | str,
    ask_price: float | str,
    bid_size: float | str,
    ask_size: float | str,
) -> dict[str, float]:
    """Return bid, ask and total best-level depth in quote-currency notional."""
    bid, ask = _prices(bid_price, ask_price)
    bid_notional = bid * _number(bid_size, "bid_size")
    ask_notional = ask * _number(ask_size, "ask_size")
    total = bid_notional + ask_notional
    if not all(math.isfinite(value) for value in (bid_notional, ask_notional, total)):
        raise ValueError("Notional depth exceeds the finite float range")
    return {"bid_notional": bid_notional, "ask_notional": ask_notional, "total_notional": total}
