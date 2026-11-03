"""Hand-checkable, entirely synthetic inputs; no exchange connection."""

from pprint import pprint

from market_microstructure import (
    bid_ask_spread,
    notional_depth,
    order_book_imbalance,
    trade_imbalance,
)


def main():
    print("SYNTHETIC_TEST_ONLY — invented quotes and trades; no market evidence")
    print("Synthetic BBO: bid=99 x 3, ask=101 x 1")
    pprint(bid_ask_spread("99", "101"))
    print("Best-level size imbalance:", order_book_imbalance("3", "1"))
    pprint(notional_depth("99", "101", "3", "1"))

    # Tiny integer nanosecond values make the time boundaries easy to inspect.
    trades = [
        {"timestamp": 95, "ingested_at": 96, "price": "100", "size": "3", "side": "buy"},
        {"timestamp": 100, "ingested_at": 100, "price": "100", "size": "1", "side": "sell"},
        {"timestamp": 99, "ingested_at": 101, "price": "100", "size": "9", "side": "sell"},
        {"timestamp": 101, "ingested_at": 102, "price": "100", "size": "9", "side": "sell"},
        {"timestamp": 90, "ingested_at": 90, "price": "100", "size": "9", "side": "buy"},
    ]
    print("Synthetic trades: event window (90, 100], receipt <= 100")
    pprint(trade_imbalance(trades, decision_ns=100, window_ns=10))
    print("No observed flow:")
    pprint(trade_imbalance([], decision_ns=100, window_ns=10))


if __name__ == "__main__":
    main()
