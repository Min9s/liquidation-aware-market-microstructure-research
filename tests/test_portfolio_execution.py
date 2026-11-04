from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.portfolio_execution import NS, synthetic_inputs
from market_microstructure import execution
from market_microstructure.execution import replay_execution

BTC = "SYNTH-BTC-USDT"
ETH = "SYNTH-ETH-USDT"


def test_shared_collateral_rejection_then_partial_close_releases_margin():
    bundle = synthetic_inputs()
    before = deepcopy(bundle)
    result = replay_execution(bundle)
    assert [r["status"] for r in result["orders"]] == ["filled", "rejected", "filled", "filled"]
    rejected = result["orders"][1]
    assert rejected["reason"] == "insufficient_collateral"
    risk = rejected["risk"]
    # BTC costs505, marked500: equity995. ETH costs550, marked500: projected945.
    assert Decimal(risk["before"]["cash"]) == 1000
    assert Decimal(risk["before"]["equity"]) == 995
    assert Decimal(risk["before"]["reserved_margin"]) == 505
    assert Decimal(risk["projected"]["equity"]) == 945
    assert Decimal(risk["projected"]["required_collateral"]) == 1055
    assert Decimal(risk["projected"]["collateral_headroom"]) == -110
    assert risk["status"] == "rejected" and risk["after"] is None
    close, reopened = result["orders"][2:]
    assert close["risk"]["status"] == "reduce_only"
    assert Decimal(close["risk"]["after"]["reserved_margin"]) == 303
    assert Decimal(reopened["risk"]["before"]["equity"]) == 1043
    assert Decimal(reopened["risk"]["projected"]["collateral_headroom"]) == 140
    # Rejected ETH50 did not consume its only50 asks; retry gets the full quantity.
    assert Decimal(reopened["filled_quantity"]) == 50
    final = result["terminal_account"]
    assert {k: Decimal(final[k]) for k in ("cash", "equity", "reserved_margin", "gross_notional")} == {
        "cash": Decimal(1016), "equity": Decimal(993),
        "reserved_margin": Decimal(853), "gross_notional": Decimal(830),
    }
    assert Decimal(result["terminal_positions"][BTC]["quantity"]) == 3
    assert Decimal(result["terminal_positions"][ETH]["quantity"]) == 50
    assert all(result["reconciliation"].values()) and bundle == before


def test_competing_same_time_orders_use_one_account_and_stable_id_priority():
    bundle = synthetic_inputs()
    btc, eth = bundle["orders"][0], bundle["orders"][2]
    btc["order_id"] = "a_btc"
    eth.update(order_id="b_eth", decision_ns=0, signal_available_ns=0, expires_ns=2 * NS)
    bundle["orders"] = [btc, eth]
    result = replay_execution(bundle)
    assert [r["status"] for r in result["orders"]] == ["filled", "rejected"]
    bundle["orders"].reverse()
    bundle["quotes"].reverse()
    bundle["instruments"].reverse()
    reversed_result = replay_execution(bundle)
    for key in ("orders", "fills", "terminal_account", "terminal_positions"):
        assert reversed_result[key] == result[key]
    # ETH would be affordable with the same policy and its own standalone entry.
    bundle["orders"] = [eth]
    assert replay_execution(bundle)["orders"][0]["status"] == "filled"


def delayed_entry_bundle():
    bundle = synthetic_inputs()
    btc_open = bundle["orders"][0]
    eth_open = bundle["orders"][-1]
    eth_open["quantity"] = "10"
    bundle["orders"] = [btc_open, eth_open]
    bundle["policy"]["max_quote_age_ns"] = 2 * NS
    # Fresh target quote at t3, while held BTC's last quote was at t0.
    bundle["quotes"] = [q for q in bundle["quotes"] if q["quote_id"] != "q3"]
    bundle["quotes"][-1].update(timestamp_ns=3 * NS, available_ns=3 * NS)
    return bundle


def test_stale_held_symbol_blocks_entry_until_a_new_quote_arrives():
    bundle = delayed_entry_bundle()
    result = replay_execution(bundle)
    order = result["orders"][-1]
    assert order["reason"] == "portfolio_mark_unavailable"
    assert order["risk"]["projected"] is None
    state = order["risk"]["before"]
    assert state["equity"] is None and state["gross_notional"] is None
    assert state["unavailable_symbols"] == [BTC]
    assert state["marks"][BTC]["reason"] == "stale_quote"
    assert Decimal(state["reserved_margin"]) == 505
    assert Decimal(state["cash"]) == 1000
    refresh = deepcopy(bundle["quotes"][0])
    refresh.update(quote_id="btc4", timestamp_ns=4 * NS, available_ns=4 * NS)
    bundle["quotes"].append(refresh)
    assert replay_execution(bundle)["orders"][-1]["status"] == "filled"


@pytest.mark.parametrize("problem", ["invalid_quote", "session_changed"])
def test_unusable_held_quote_does_not_revive_previous_state(problem):
    bundle = delayed_entry_bundle()
    update = deepcopy(bundle["quotes"][0])
    update.update(quote_id="btc3", timestamp_ns=3 * NS, available_ns=3 * NS)
    if problem == "invalid_quote":
        update.update(valid=False, bids=[], asks=[])
    else:
        update["session_id"] = "s2"
    bundle["quotes"].append(update)
    order = replay_execution(bundle)["orders"][-1]
    assert order["reason"] == "portfolio_mark_unavailable"
    assert order["risk"]["before"]["marks"][BTC]["reason"] == problem


def test_missing_or_stale_unheld_instrument_does_not_block_new_entry():
    bundle = delayed_entry_bundle()
    bundle["orders"] = [bundle["orders"][-1]]
    # BTC is registered but unheld and has no quotes at all.
    bundle["quotes"] = [q for q in bundle["quotes"] if q["symbol"] == ETH]
    result = replay_execution(bundle)
    assert result["orders"][0]["status"] == "filled"
    assert result["orders"][0]["risk"]["before"]["unavailable_symbols"] == []


def test_reduce_only_close_allowed_when_another_held_symbol_has_stale_mark():
    bundle = synthetic_inputs()
    bundle["policy"].update(initial_cash="5000", max_quote_age_ns=2 * NS)
    # BTC and ETH enter; at t4 BTC is fresh but held ETH is stale.
    bundle["orders"] = bundle["orders"][:3]
    result = replay_execution(bundle)
    close = result["orders"][-1]
    assert close["status"] == "filled" and close["risk"]["status"] == "reduce_only"
    assert close["risk"]["before"]["unavailable_symbols"] == [ETH]
    assert close["risk"]["after"]["equity"] is None
    assert Decimal(close["risk"]["before"]["reserved_margin"]) == 1055
    assert Decimal(close["risk"]["after"]["reserved_margin"]) == 853
    assert Decimal(result["terminal_cash"]) == 5016
    assert all(result["reconciliation"].values())


@pytest.mark.parametrize("initial,expected", [("204", "filled"), ("203.99999999", "rejected")])
def test_collateral_equality_and_one_quantum_below(initial, expected):
    bundle = synthetic_inputs()
    bundle["policy"]["initial_cash"] = initial
    bundle["orders"] = [bundle["orders"][0]]
    bundle["orders"][0]["quantity"] = "2"
    result = replay_execution(bundle)
    order = result["orders"][0]
    # Entry202 + spread loss2 requires cash204; exact equality passes.
    assert order["status"] == expected
    headroom = Decimal(order["risk"]["projected"]["collateral_headroom"])
    assert headroom == (0 if expected == "filled" else Decimal("-0.00000001"))


def test_entry_fee_is_included_in_projected_collateral():
    bundle = synthetic_inputs()
    bundle["policy"].update(initial_cash="204", fee_bps="5")
    bundle["orders"] = [bundle["orders"][0]]
    bundle["orders"][0]["quantity"] = "2"
    order = replay_execution(bundle)["orders"][0]
    assert order["reason"] == "insufficient_collateral"
    assert Decimal(order["risk"]["candidate"]["fee"]) == Decimal("0.101")
    assert Decimal(order["risk"]["projected"]["collateral_headroom"]) == Decimal("-0.101")


def test_long_and_short_gross_exposures_are_not_netted():
    bundle = synthetic_inputs()
    bundle["policy"]["initial_cash"] = "550"
    btc, eth = bundle["orders"][0], bundle["orders"][2]
    btc["quantity"] = "4"
    eth.update(direction="short", quantity="20")
    bundle["orders"] = [btc, eth]
    order = replay_execution(bundle)["orders"][-1]
    # Long400 + short200 = gross600 (not net200); projected equity526.
    assert Decimal(order["risk"]["projected"]["gross_notional"]) == 600
    assert Decimal(order["risk"]["projected"]["reserved_margin"]) == 584
    assert order["reason"] == "insufficient_collateral"


def test_two_symbol_round_trip_with_opposite_directions_and_fees():
    bundle = synthetic_inputs()
    bundle["policy"].update(initial_cash="5000", fee_bps="5")
    btc_open, btc_close, eth_open, eth_close = bundle["orders"]
    btc_close["quantity"] = "5"
    eth_open["direction"] = "short"
    eth_close.update(order_id="eth_close", action="close", direction="short")
    bundle["orders"] = [btc_open, btc_close, eth_open, eth_close]
    result = replay_execution(bundle)
    # BTC:5*(109-101)=40. ETH:50*(9-11)=-100.
    # Fee:(505+545+450+550)*0.0005=1.025. Cash:5000-60-1.025=4938.975.
    assert Decimal(result["realized_pnl"]) == -60
    assert Decimal(result["fees"]) == Decimal("1.025")
    assert Decimal(result["terminal_cash"]) == Decimal("4938.975")
    assert result["terminal_positions"] == {} and result["terminal_flat"]
    assert Decimal(result["terminal_account"]["reserved_margin"]) == 0
    assert Decimal(result["terminal_equity"]) == Decimal("4938.975")
    assert all(result["reconciliation"].values())


def test_new_entry_revalues_existing_position_with_current_available_price():
    bundle = synthetic_inputs()
    eth = bundle["orders"][-1]
    eth["quantity"] = "40"
    bundle["orders"] = [bundle["orders"][0], eth]
    # BTC falls to mid50 before ETH arrival: unrealized=-255, equity745.
    bundle["quotes"][1].update(bids=[["49", "10"]], asks=[["51", "10"]])
    order = replay_execution(bundle)["orders"][-1]
    assert Decimal(order["risk"]["before"]["equity"]) == 745
    assert Decimal(order["risk"]["projected"]["equity"]) == 705
    assert Decimal(order["risk"]["projected"]["required_collateral"]) == 945
    assert order["reason"] == "insufficient_collateral"


@pytest.mark.parametrize("mutation", [
    lambda b: b["instruments"].append(deepcopy(b["instruments"][0])),
    lambda b: b["orders"][0].update(symbol="UNKNOWN"),
    lambda b: b["quotes"][0].pop("symbol"),
    lambda b: b["orders"][0].update(quantity="0.5"),
    lambda b: b.update(instrument=b["instruments"][0]),
    lambda b: b["orders"].append(deepcopy(b["orders"][0])),
    lambda b: b["quotes"].append(dict(b["quotes"][0], quote_id="conflicting")),
])
def test_invalid_portfolio_contracts_are_rejected(mutation):
    bundle = synthetic_inputs()
    mutation(bundle)
    with pytest.raises(ValueError):
        replay_execution(bundle)


def test_terminal_positions_and_reserved_margin_are_reconciled_per_symbol(monkeypatch):
    original = execution._replay

    def corrupted(bundle, instruments):
        result = original(bundle, instruments)
        result["terminal_positions"][ETH]["entry_notional"] += 1
        return result

    monkeypatch.setattr(execution, "_replay", corrupted)
    with pytest.raises(ValueError, match="ledger"):
        replay_execution(synthetic_inputs())


def test_portfolio_demo_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/portfolio_execution.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "insufficient_collateral" in run.stdout and "equity=993" in run.stdout
