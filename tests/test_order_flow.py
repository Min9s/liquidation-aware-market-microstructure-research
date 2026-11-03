import pytest

from market_microstructure import trade_imbalance


def trade(**changes):
    return {"timestamp": 95, "ingested_at": 96, "price": "100", "size": "1", "side": "buy", **changes}


def calculate(rows, **changes):
    return trade_imbalance(rows, **{"decision_ns": 100, "window_ns": 10, **changes})


def test_hand_calculated_notional_imbalance():
    assert calculate([trade(size="3"), trade(side="sell")]) == {
        "buy_notional": 300.0, "sell_notional": 100.0, "imbalance": 0.5, "trade_count": 2
    }


def test_notional_weighting_differs_from_quantity_weighting():
    result = calculate([trade(size="2"), trade(price="200", side="sell")])
    assert result["buy_notional"] == result["sell_notional"] == 200
    assert result["imbalance"] == 0.0


@pytest.mark.parametrize(
    "timestamp,received,included",
    [(89, 90, False), (90, 90, False), (91, 91, True),
     (100, 100, True), (101, 99, False), (99, 100, True), (99, 101, False)],
)
def test_exact_event_and_receipt_boundaries(timestamp, received, included):
    result = calculate([trade(timestamp=timestamp, ingested_at=received)])
    assert result["trade_count"] == int(included)
    assert result["buy_notional"] == (100 if included else 0)
    assert result["imbalance"] == (1 if included else None)


def test_empty_and_zero_notional_are_distinct_from_balanced_positive_flow():
    empty = calculate([])
    zero_size = calculate([trade(size=0)])
    balanced = calculate([trade(), trade(side="sell")])
    assert empty["imbalance"] is zero_size["imbalance"] is None
    assert empty["trade_count"] == 0 and zero_size["trade_count"] == 1
    assert balanced["imbalance"] == 0.0


def test_sell_only_flow():
    assert calculate([trade(side="sell")])["imbalance"] == -1.0


def test_order_independence_generator_and_side_swap():
    rows = [trade(size=3), trade(side="sell")]
    assert calculate(iter(reversed(rows))) == calculate(rows)
    swapped = [{**row, "side": "sell" if row["side"] == "buy" else "buy"} for row in rows]
    assert calculate(swapped)["imbalance"] == -calculate(rows)["imbalance"]


def test_future_or_delayed_valid_trades_do_not_change_past_result():
    baseline = [trade()]
    extra = [trade(timestamp=101, ingested_at=102, side="sell"),
             trade(ingested_at=101, side="sell")]
    assert calculate(baseline + extra) == calculate(baseline)


def test_large_integer_nanosecond_boundaries_do_not_lose_precision():
    decision = 1_800_000_000_000_000_000
    rows = [trade(timestamp=decision - 1, ingested_at=decision),
            trade(timestamp=decision, ingested_at=decision + 1, side="sell")]
    result = calculate(rows, decision_ns=decision, window_ns=1)
    assert result["trade_count"] == 0
    result = calculate(rows, decision_ns=decision, window_ns=2)
    assert result["trade_count"] == 1 and result["imbalance"] == 1.0


def test_window_can_extend_before_zero_and_decision_can_be_zero():
    result = calculate([trade(timestamp=0, ingested_at=0)], decision_ns=0)
    assert result["trade_count"] == 1


@pytest.mark.parametrize("decision", [-1, 1.5, "100", True, None])
def test_invalid_decision_timestamp(decision):
    with pytest.raises(ValueError):
        calculate([], decision_ns=decision)


@pytest.mark.parametrize("window", [0, -1, 1.5, "10", True, None])
def test_invalid_window(window):
    with pytest.raises(ValueError):
        calculate([], window_ns=window)


@pytest.mark.parametrize("field", ["timestamp", "ingested_at"])
@pytest.mark.parametrize("bad", [-1, 1.5, "95", True, None])
def test_invalid_trade_timestamps(field, bad):
    with pytest.raises(ValueError):
        calculate([trade(**{field: bad})])


@pytest.mark.parametrize("field", ["timestamp", "ingested_at", "price", "size", "side"])
def test_missing_required_field(field):
    row = trade()
    del row[field]
    with pytest.raises(ValueError):
        calculate([row])


@pytest.mark.parametrize("row", [None, [], "not a trade"])
def test_non_mapping_trade(row):
    with pytest.raises(ValueError):
        calculate([row])


@pytest.mark.parametrize("changes", [
    {"price": 0}, {"price": -1}, {"size": -1}, {"price": -1, "size": -1},
    {"price": "NaN"}, {"size": "Infinity"}, {"price": "bad"}, {"size": None},
    {"price": True}, {"size": False}, {"price": 10**400},
    {"side": "B"}, {"side": "unknown"}, {"side": []},
])
def test_invalid_trade_values(changes):
    with pytest.raises(ValueError):
        calculate([trade(**changes)])


def test_excluded_rows_are_still_validated():
    with pytest.raises(ValueError):
        calculate([trade(timestamp=1, ingested_at=1, price=-1)])


@pytest.mark.parametrize("rows", [
    [trade(price=1e308, size=2)],
    [trade(price=1e308), trade(price=1e308)],
    [trade(price=1e308), trade(price=1e308, side="sell")],
])
def test_product_side_sum_and_combined_sum_overflow(rows):
    with pytest.raises(ValueError):
        calculate(rows)


def test_duplicate_input_is_counted_twice():
    buy, sell = trade(), trade(side="sell")
    assert calculate([buy, sell])["imbalance"] == 0
    result = calculate([buy, buy, sell])
    assert result["trade_count"] == 3
    assert result["imbalance"] == pytest.approx(1 / 3)


def test_decimal_trade_notional_uses_float_tolerance():
    result = calculate([trade(price="0.1", size="3"), trade(price="0.3", side="sell")])
    assert result["buy_notional"] == pytest.approx(0.3)
    assert result["imbalance"] == pytest.approx(0.0, abs=1e-15)
