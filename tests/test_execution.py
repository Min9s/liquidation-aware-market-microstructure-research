from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, getcontext, localcontext
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.execution_walkthrough import NS, synthetic_inputs
from market_microstructure import execution
from market_microstructure.execution import replay_execution


def test_long_cash_and_quantity_match_independent_hand_calculation():
    bundle = synthetic_inputs()
    before = deepcopy(bundle)
    result = replay_execution(bundle)
    assert [Decimal(f["notional"]) for f in result["fills"]] == [202, 218]
    assert [Decimal(f["fee"]) for f in result["fills"]] == [Decimal("0.101"), Decimal("0.109")]
    assert Decimal(result["realized_pnl"]) == 16
    assert Decimal(result["terminal_cash"]) == Decimal("1015.79")
    assert Decimal(result["terminal_position"]["quantity"]) == 0
    assert result["terminal_flat"] and all(result["reconciliation"].values())
    assert result["actual_orders_sent"] == 0 and bundle == before


def test_short_direction_and_fee_signs():
    bundle = synthetic_inputs()
    for row in bundle["orders"]:
        row["direction"] = "short"
    bundle["quotes"][1].update(bids=[["87", "10"]], asks=[["89", "10"]])
    result = replay_execution(bundle)
    assert [Decimal(f["notional"]) for f in result["fills"]] == [198, 178]
    assert Decimal(result["terminal_cash"]) == Decimal("1019.812")
    assert Decimal(result["realized_pnl"]) == 20


def test_future_quote_is_not_used_and_future_price_cannot_change_entry():
    bundle = synthetic_inputs()
    baseline = replay_execution(bundle)
    bundle["quotes"][1].update(bids=[["909", "10"]], asks=[["911", "10"]])
    assert replay_execution(bundle)["fills"][0] == baseline["fills"][0]
    bundle["quotes"][0]["available_ns"] = NS + 1
    result = replay_execution(bundle)
    assert result["orders"][0]["status"] == "rejected" and not result["fills"]


@pytest.mark.parametrize("age,filled", [(NS, True), (NS - 1, False)])
def test_quote_age_boundary(age, filled):
    bundle = synthetic_inputs()
    bundle["policy"]["max_quote_age_ns"] = age
    assert (replay_execution(bundle)["orders"][0]["status"] == "filled") is filled


def test_quote_received_exactly_at_arrival_and_older_delayed_quote():
    bundle = synthetic_inputs()
    bundle["quotes"][0]["available_ns"] = NS
    older = deepcopy(bundle["quotes"][0])
    older.update(quote_id="old", timestamp_ns=NS, available_ns=4 * NS,
                 bids=[["49", "10"]], asks=[["51", "10"]])
    bundle["quotes"].append(older)
    result = replay_execution(bundle)
    assert result["fills"][0]["quote_available_ns"] == NS
    assert result["fills"][1]["quote_id"] == "q3"
    assert result["ignored_quotes"] == [{"quote_id": "old", "reason": "older_event_timestamp"}]


def test_depth_partial_fill_and_reduce_only_close_cap():
    bundle = synthetic_inputs()
    bundle["quotes"][0]["asks"] = [["101", "1"], ["102", "1"]]
    for row in bundle["orders"]:
        row["quantity"] = "3"
    result = replay_execution(bundle)
    assert [Decimal(f["quantity"]) for f in result["fills"]] == [2, 2]
    assert Decimal(result["fills"][0]["vwap"]) == Decimal("101.5")
    assert [r["reason"] for r in result["orders"]] == ["ioc_depth_partial", "close_capped_to_position"]
    assert all(Decimal(r["unfilled_quantity"]) == 1 for r in result["orders"])
    assert Decimal(result["terminal_cash"]) == Decimal("1014.7895")


def test_rejected_entry_does_not_consume_depth_or_charge_fee():
    bundle = synthetic_inputs()
    bundle["quotes"][0]["asks"] = [["101", "2"]]
    large = deepcopy(bundle["orders"][0])
    large.update(order_id="aaa_large", quantity="10")
    # Entry below min notional is rejected before committing any of the two units.
    bundle["instrument"]["min_notional"] = "203"
    bundle["orders"].insert(0, large)
    result = replay_execution(bundle)
    assert not result["fills"] and Decimal(result["fees"]) == 0
    assert result["orders"][0]["reason"] == "filled_entry_below_minimum_notional"
    # A collateral failure also must not consume the depth needed by the smaller entry.
    bundle = synthetic_inputs()
    large.update(quantity="10")
    bundle["orders"].insert(0, large)
    result = replay_execution(bundle)
    assert result["orders"][0]["reason"] == "insufficient_collateral"
    assert result["orders"][1]["status"] == "filled"
    assert len(result["fills"]) == 2


def test_two_closes_share_remaining_depth_and_leave_open_position():
    bundle = synthetic_inputs()
    bundle["quotes"][1]["bids"] = [["109", "1"]]
    second = deepcopy(bundle["orders"][1])
    second["order_id"] = "z_second_close"
    bundle["orders"].append(second)
    result = replay_execution(bundle)
    assert result["orders"][-1]["reason"] == "no_remaining_displayed_depth"
    assert Decimal(result["terminal_position"]["quantity"]) == 1
    assert Decimal(result["terminal_position"]["entry_notional"]) == 101
    assert not result["terminal_flat"]


@pytest.mark.parametrize("mode", ["invalid", "session", "late_exit"])
def test_failed_exit_is_not_an_invented_flat_position(mode):
    bundle = synthetic_inputs()
    if mode == "invalid":
        bundle["quotes"][1].update(valid=False, bids=[], asks=[])
    elif mode == "session":
        bundle["quotes"][1]["session_id"] = "new_session"
    else:
        bundle["orders"][1].update(decision_ns=5 * NS, signal_available_ns=5 * NS,
                                   expires_ns=6 * NS)
        bundle["policy"]["max_quote_age_ns"] = NS
    result = replay_execution(bundle)
    assert not result["terminal_flat"] and len(result["fills"]) == 1
    assert result["terminal_equity"] is None
    assert result["terminal_unrealized_pnl"] is None


def test_equal_time_priority_is_stable_when_input_is_reversed():
    bundle = synthetic_inputs()
    opening = deepcopy(bundle["orders"][0])
    opening.update(order_id="aaa_reopen", decision_ns=3 * NS, signal_available_ns=3 * NS,
                   expires_ns=5 * NS)
    bundle["orders"].append(opening)
    result = replay_execution(bundle)
    bundle["quotes"].reverse()
    bundle["orders"].reverse()
    assert replay_execution(bundle) == result
    assert [r["order_id"] for r in result["orders"]] == ["open", "close", "aaa_reopen"]


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(source_kind="REAL_OBSERVED"),
    lambda b: b["orders"][0].update(signal_available_ns=1),
    lambda b: b["orders"][0].update(quantity=True),
    lambda b: b["orders"][0].update(quantity="0.5"),
    lambda b: b["quotes"][0].update(timestamp_ns=True),
    lambda b: b["quotes"][0].update(asks=[["98", "1"]]),
    lambda b: b["quotes"][0].update(asks=[["NaN", "1"]]),
    lambda b: b["quotes"].append(deepcopy(b["quotes"][0])),
    lambda b: b["policy"].update(initial_cash="1000.000000001"),
    lambda b: b["policy"].update(fee_bps="1e999999"),
])
def test_bad_inputs_fail_before_replay(mutation):
    bundle = synthetic_inputs()
    mutation(bundle)
    with pytest.raises(ValueError):
        replay_execution(bundle)


def test_half_even_fee_rounding_and_decimal_context_are_explicit():
    bundle = synthetic_inputs()
    bundle["policy"]["fee_bps"] = "0.00005"
    # 202 * 0.00005/10000 = 0.000001010; half-even quantum rounds normally.
    expected = replay_execution(bundle)
    assert Decimal(expected["fills"][0]["fee"]) == Decimal("0.00000101")
    with localcontext() as context:
        context.prec = 6
        assert replay_execution(bundle) == expected
        assert getcontext().prec == 6


@pytest.mark.parametrize("fee_bps,expected", [("0.00005", "0"), ("0.00015", "0.00000002")])
def test_fee_exact_half_quantum_ties(fee_bps, expected):
    bundle = synthetic_inputs()
    bundle["instrument"].update(price_tick="0.1", min_notional="1")
    bundle["policy"]["fee_bps"] = fee_bps
    bundle["quotes"][0].update(bids=[["0.9", "10"]], asks=[["1", "10"]])
    bundle["orders"] = [bundle["orders"][0]]
    bundle["orders"][0]["quantity"] = "1"
    assert Decimal(replay_execution(bundle)["fees"]) == Decimal(expected)


def test_repeating_vwap_does_not_inherit_callers_rounding():
    bundle = synthetic_inputs()
    bundle["quotes"][0]["asks"] = [["101", "1"], ["102", "2"]]
    bundle["orders"][0]["quantity"] = "3"
    expected = replay_execution(bundle)
    with localcontext() as context:
        context.rounding = ROUND_DOWN
        assert replay_execution(bundle) == expected
        assert context.rounding == ROUND_DOWN


@pytest.mark.parametrize("expiry,status", [(NS, "filled"), (NS - 1, "rejected")])
def test_order_expiry_boundary(expiry, status):
    bundle = synthetic_inputs()
    bundle["orders"][0]["expires_ns"] = expiry
    assert replay_execution(bundle)["orders"][0]["status"] == status


@pytest.mark.parametrize("field", ["cash_after", "gross_realized_pnl", "fee", "quantity"])
def test_reconciliation_detects_corrupted_fill_facts(monkeypatch, field):
    original = execution._replay

    def corrupted(bundle, minimum):
        result = original(bundle, minimum)
        result["fills"][-1][field] += Decimal("1")
        return result

    monkeypatch.setattr(execution, "_replay", corrupted)
    with pytest.raises(ValueError, match="ledger"):
        replay_execution(synthetic_inputs())


def test_demo_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/execution_walkthrough.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout and "1015.79000000" in run.stdout
