"""Notional trade imbalance using both event time and receipt availability."""

import math
from collections.abc import Iterable, Mapping


def trade_imbalance(
    rows: Iterable[Mapping[str, object]], *, decision_ns: int, window_ns: int
) -> dict[str, float | int | None]:
    """Aggregate buy/sell notional in (decision_ns-window_ns, decision_ns].

    A trade must also have ingested_at <= decision_ns. All supplied rows are
    validated, including excluded rows. Side must be exactly 'buy' or 'sell'.
    Zero total notional has imbalance None, while balanced positive flow is 0.
    """
    if type(decision_ns) is not int or decision_ns < 0:
        raise ValueError("decision_ns must be a nonnegative integer in nanoseconds")
    if type(window_ns) is not int or window_ns <= 0:
        raise ValueError("window_ns must be a positive integer in nanoseconds")

    buys, sells = [], []
    trade_count = 0
    for row in rows:
        try:
            timestamp, received = row["timestamp"], row["ingested_at"]
            raw_price, raw_size, side = row["price"], row["size"], row["side"]
        except (KeyError, TypeError) as error:
            raise ValueError("Each trade must contain timestamp, ingested_at, price, size, side") from error
        if type(timestamp) is not int or type(received) is not int or min(timestamp, received) < 0:
            raise ValueError("Trade timestamps must be nonnegative integers in nanoseconds")
        if isinstance(raw_price, bool) or isinstance(raw_size, bool):
            raise ValueError("Trade price and size must be numbers, not bool")
        try:
            price, size = float(raw_price), float(raw_size)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Trade price and size must be finite numbers") from error
        if not math.isfinite(price) or price <= 0 or not math.isfinite(size) or size < 0:
            raise ValueError("Trade price must be positive and size nonnegative; both must be finite")
        if side not in ("buy", "sell"):
            raise ValueError("Trade side must be 'buy' or 'sell'")
        notional = price * size
        if not math.isfinite(notional):
            raise ValueError("Trade notional exceeds the finite float range")

        if decision_ns - window_ns < timestamp <= decision_ns and received <= decision_ns:
            (buys if side == "buy" else sells).append(notional)
            trade_count += 1

    try:
        buy, sell = math.fsum(buys), math.fsum(sells)
    except OverflowError as error:
        raise ValueError("Aggregated trade notional exceeds the finite float range") from error
    total = buy + sell
    if not math.isfinite(total):
        raise ValueError("Total trade notional exceeds the finite float range")
    return {
        "buy_notional": buy,
        "sell_notional": sell,
        "imbalance": (buy - sell) / total if total else None,
        "trade_count": trade_count,
    }
