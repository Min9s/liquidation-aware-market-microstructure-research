import math

import pytest

from market_microstructure import bid_ask_spread, notional_depth, order_book_imbalance


def test_hand_calculated_best_quote():
    assert bid_ask_spread("99", "101") == {"mid": 100.0, "absolute": 2.0, "bps": 200.0}
    assert order_book_imbalance("3", "1") == 0.5
    assert notional_depth("99", "101", "3", "1") == {
        "bid_notional": 297.0, "ask_notional": 101.0, "total_notional": 398.0
    }


def test_locked_book_and_empty_depth():
    assert bid_ask_spread(100, 100) == {"mid": 100.0, "absolute": 0.0, "bps": 0.0}
    assert notional_depth(100, 100, 0, 0)["total_notional"] == 0.0


@pytest.mark.parametrize("bid,ask", [(101, 99), (0, 100), (-1, 100), (1, 0), (1, -1)])
def test_invalid_price_pair_rejected_by_both_price_metrics(bid, ask):
    with pytest.raises(ValueError):
        bid_ask_spread(bid, ask)
    with pytest.raises(ValueError):
        notional_depth(bid, ask, 1, 1)


@pytest.mark.parametrize("bad", [None, "bad", "NaN", "Infinity", -math.inf, True, 10**400])
def test_bad_numeric_values_are_rejected_on_either_side(bad):
    for bid, ask in [(bad, 101), (99, bad)]:
        with pytest.raises(ValueError):
            bid_ask_spread(bid, ask)
    for bid_size, ask_size in [(bad, 1), (1, bad)]:
        with pytest.raises(ValueError):
            order_book_imbalance(bid_size, ask_size)
        with pytest.raises(ValueError):
            notional_depth(99, 101, bid_size, ask_size)


@pytest.mark.parametrize("bid_size,ask_size,expected", [(0, 0, None), (3, 0, 1), (0, 3, -1), (2, 2, 0)])
def test_imbalance_zero_and_one_sided_sizes(bid_size, ask_size, expected):
    assert order_book_imbalance(bid_size, ask_size) == expected


@pytest.mark.parametrize("sizes", [(-1, 1), (1, -1), (-1, -1)])
def test_negative_sizes_rejected(sizes):
    with pytest.raises(ValueError):
        order_book_imbalance(*sizes)
    with pytest.raises(ValueError):
        notional_depth(99, 101, *sizes)


def test_size_scaling_and_side_swap():
    assert order_book_imbalance(30, 10) == order_book_imbalance(3, 1)
    assert order_book_imbalance(1, 3) == -order_book_imbalance(3, 1)
    assert notional_depth(99, 101, 30, 10)["total_notional"] == 3980


def test_price_scaling_changes_absolute_spread_but_not_bps():
    original = bid_ask_spread(99, 101)
    scaled = bid_ask_spread(990, 1010)
    assert scaled["absolute"] == 10 * original["absolute"]
    assert scaled["bps"] == original["bps"]


def test_midpoint_stays_finite_for_large_finite_prices():
    assert bid_ask_spread(1e308, 1e308) == {"mid": 1e308, "absolute": 0.0, "bps": 0.0}


def test_one_sided_notional_depth():
    assert notional_depth(99, 101, 0, 2) == {
        "bid_notional": 0.0, "ask_notional": 202.0, "total_notional": 202.0
    }


def test_overflow_in_sizes_and_notional_is_explicit():
    with pytest.raises(ValueError):
        order_book_imbalance(1e308, 1e308)
    with pytest.raises(ValueError):
        notional_depth(99, 101, 1e308, 1)
    with pytest.raises(ValueError):
        notional_depth(1e308, 1e308, 1, 1)
