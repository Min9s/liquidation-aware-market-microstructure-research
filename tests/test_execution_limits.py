from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.execution_walkthrough import NS, synthetic_inputs
from examples.portfolio_execution import synthetic_inputs as portfolio_inputs
from examples.risk_limits import limited_inputs
from market_microstructure.execution import replay_execution
from market_microstructure.execution_journal import ExecutionJournal
from market_microstructure.execution_monitor import monitor_execution

BTC = "SYNTH-BTC-USDT"
ETH = "SYNTH-ETH-USDT"


@pytest.mark.parametrize("name,value", [
    ("max_order_notional", "202"), ("max_symbol_gross", "200"), ("max_portfolio_gross", "200")])
def test_exact_limit_passes_and_one_quantum_below_rejects_with_sufficient_collateral(name, value):
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    limits = bundle["policy"]["risk_limits"]
    target = limits["max_symbol_gross"] if name == "max_symbol_gross" else limits
    key = BTC if name == "max_symbol_gross" else name
    target[key] = value
    assert replay_execution(bundle)["orders"][0]["status"] == "filled"
    target[key] = str(Decimal(value) - Decimal("0.00000001"))
    result = replay_execution(bundle)
    order = result["orders"][0]
    assert order["reason"] == name + "_exceeded"
    assert Decimal(order["risk"]["projected"]["collateral_headroom"]) > 0
    check = next(c for c in order["risk"]["projected"]["limit_checks"] if c["name"] == name)
    assert Decimal(check["headroom"]) == Decimal("-0.00000001")
    assert not result["fills"] and Decimal(result["terminal_cash"]) == 1000


def test_partial_depth_does_not_hide_oversized_requested_order():
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["orders"][0]["quantity"] = "10"
    bundle["quotes"][0]["asks"] = [["101", "1"]]
    result = replay_execution(bundle)
    order = result["orders"][0]
    assert order["reason"] == "max_order_notional_exceeded"
    assert Decimal(order["risk"]["candidate"]["notional"]) == 101
    check = order["risk"]["projected"]["limit_checks"][0]
    assert Decimal(check["value"]) == 1010  # requested10 * reference ask101, not filled101
    bundle["policy"]["risk_limits"]["max_order_notional"] = "1100"
    bundle["policy"]["risk_limits"]["max_symbol_gross"][BTC] = "100"
    result = replay_execution(bundle)
    assert result["orders"][0]["status"] == "partial"
    assert Decimal(result["terminal_account"]["symbol_gross"][BTC]) == 110  # terminal mid110


def test_order_assessment_covers_deeper_fill_cost_above_reference_estimate():
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"][0]["asks"] = [["101", "1"], ["201", "1"]]
    bundle["policy"]["risk_limits"]["max_order_notional"] = "250"
    order = replay_execution(bundle)["orders"][0]
    assert order["reason"] == "max_order_notional_exceeded"
    assert Decimal(order["risk"]["projected"]["limit_checks"][0]["value"]) == 302


def test_rejected_limit_order_does_not_consume_depth_needed_by_smaller_order():
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["orders"][0]["order_id"] = "z_small"
    large = deepcopy(bundle["orders"][0])
    large.update(order_id="a_large", quantity="3")
    bundle["orders"].insert(0, large)
    bundle["quotes"][0]["asks"] = [["101", "3"]]
    result = replay_execution(bundle)
    assert result["orders"][0]["reason"] == "max_symbol_gross_exceeded"
    assert result["orders"][1]["status"] == "filled"
    assert len(result["fills"]) == 1 and Decimal(result["fees"]) == Decimal("0.101")


def test_shared_portfolio_cap_and_reduce_only_close_exemption():
    bundle = portfolio_inputs()
    bundle["policy"].update(initial_cash="5000", risk_limits={
        "max_order_notional": "600", "max_symbol_gross": {BTC: "1000", ETH: "1000"},
        "max_portfolio_gross": "900"})
    result = replay_execution(bundle)
    assert result["orders"][1]["reason"] == "max_portfolio_gross_exceeded"
    assert result["orders"][2]["risk"]["status"] == "reduce_only"
    assert result["orders"][3]["status"] == "filled"  # after BTC partial close, gross830


def test_reduce_only_close_over_order_cap_is_allowed_while_exposure_is_breached():
    bundle = limited_inputs()
    bundle["policy"]["risk_limits"]["max_order_notional"] = "202"
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    result = replay_execution(bundle)
    assert result["orders"][1]["status"] == "filled"  # close notional298 exceeds202
    assert result["orders"][1]["risk"]["status"] == "reduce_only"
    assert result["terminal_positions"] == {}
    observed = monitor_execution(bundle)
    assert observed["risk_events"][0]["to_status"] == "exposure_limit_breach"
    assert observed["risk_events"][-1]["change"] == "resolved"


def test_existing_symbol_violation_blocks_entry_in_another_symbol():
    bundle = portfolio_inputs()
    bundle["policy"].update(initial_cash="5000", risk_limits={
        "max_order_notional": "5000", "max_symbol_gross": {BTC: "600", ETH: "1000"},
        "max_portfolio_gross": "5000"})
    bundle["orders"] = [bundle["orders"][0], bundle["orders"][-1]]
    bundle["orders"][-1]["quantity"] = "10"
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    result = replay_execution(bundle)
    order = result["orders"][-1]
    assert order["reason"] == "max_symbol_gross_exceeded"
    failed = [c for c in order["risk"]["projected"]["limit_checks"] if c["passed"] is False]
    assert failed[0]["symbol"] == BTC and Decimal(failed[0]["value"]) == 750


def test_holding_limit_breach_and_recovery_do_not_change_execution():
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    recovery = deepcopy(bundle["quotes"][0])
    recovery.update(quote_id="q4", timestamp_ns=4 * NS, available_ns=4 * NS)
    bundle["quotes"].append(recovery)
    before = deepcopy(bundle)
    observed = monitor_execution(bundle)
    started, resolved = observed["risk_events"]
    assert started["to_status"] == "exposure_limit_breach" and started["timestamp_ns"] == 3 * NS
    failed = [c for c in started["account"]["limit_checks"] if c["passed"] is False]
    assert failed[0]["name"] == "max_symbol_gross"
    assert Decimal(failed[0]["value"]) == 300 and Decimal(failed[0]["headroom"]) == -50
    assert resolved["timestamp_ns"] == 4 * NS and resolved["change"] == "resolved"
    assert observed["execution"] == replay_execution(bundle) and bundle == before


def test_unknown_gross_limit_is_not_reported_as_passed_or_zero():
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"] = [bundle["quotes"][0]]
    bundle["policy"]["max_quote_age_ns"] = NS
    result = monitor_execution(bundle)
    assert result["terminal_status"] == "valuation_unavailable"
    for check in result["observations"][-1]["account"]["limit_checks"]:
        assert check["value"] is None and check["headroom"] is None and check["passed"] is None


def test_changed_breached_cap_set_stays_in_one_episode_until_all_limits_recover():
    bundle = limited_inputs()
    bundle["policy"]["risk_limits"]["max_portfolio_gross"] = "270"
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    for second, bid, ask in [(4, "129", "131"), (5, "99", "101")]:
        quote = deepcopy(bundle["quotes"][0])
        quote.update(quote_id=f"q{second}", timestamp_ns=second * NS, available_ns=second * NS,
                     bids=[[bid, "10"]], asks=[[ask, "10"]])
        bundle["quotes"].append(quote)
    result = monitor_execution(bundle)
    assert [event["change"] for event in result["risk_events"]] == ["started", "changed", "resolved"]
    changed = result["risk_events"][1]
    assert changed["from_status"] == changed["to_status"] == "exposure_limit_breach"
    assert len(result["episodes"]) == 1 and result["terminal_status"] == "healthy"


def test_known_symbol_violation_remains_visible_when_other_symbol_cannot_be_valued():
    bundle = portfolio_inputs()
    bundle["policy"].update(initial_cash="5000", max_quote_age_ns=2 * NS, risk_limits={
        "max_order_notional": "5000", "max_symbol_gross": {BTC: "600", ETH: "1000"},
        "max_portfolio_gross": "5000"})
    bundle["orders"] = [bundle["orders"][0], bundle["orders"][2]]
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    result = monitor_execution(bundle)
    assert result["terminal_status"] == "valuation_unavailable"
    checks = result["observations"][-1]["account"]["limit_checks"]
    btc = next(c for c in checks if c["symbol"] == BTC)
    eth = next(c for c in checks if c["symbol"] == ETH)
    assert btc["passed"] is False and Decimal(btc["value"]) == 750
    assert eth["passed"] is None and checks[-1]["passed"] is None


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(policy=None),
    lambda b: b["policy"].update(risk_limits=None),
    lambda b: b["policy"]["risk_limits"].update(max_order_notional=True),
    lambda b: b["policy"]["risk_limits"].update(max_order_notional="0"),
    lambda b: b["policy"]["risk_limits"].update(max_portfolio_gross="NaN"),
    lambda b: b["policy"]["risk_limits"].update(max_symbol_gross={}),
    lambda b: b["policy"]["risk_limits"]["max_symbol_gross"].update(UNKNOWN="100"),
    lambda b: b["policy"]["risk_limits"]["max_symbol_gross"].update({BTC: "-1"}),
])
def test_invalid_limits_fail_before_execution(mutation):
    bundle = limited_inputs()
    mutation(bundle)
    with pytest.raises(ValueError):
        replay_execution(bundle)


def test_frozen_journal_keeps_limit_rejections_and_policy_identity(tmp_path):
    bundle = limited_inputs()
    bundle["policy"]["risk_limits"]["max_symbol_gross"][BTC] = "180"
    path = tmp_path / "limits.sqlite"
    with ExecutionJournal.create(path, bundle) as store:
        for event in store.events():
            store.record(event)
    with ExecutionJournal.open(path) as store:
        assert store.recover()["account"]["positions"] == {}
        assert Decimal(store.recover()["account"]["cash"]) == 1000
        assert store.events()[0]["order"]["reason"] == "max_symbol_gross_exceeded"


def test_no_limits_preserves_existing_default_and_high_caps_preserve_fills():
    baseline = replay_execution(synthetic_inputs())
    with_caps = replay_execution(limited_inputs())
    assert "limit_checks" not in baseline["terminal_account"]
    assert with_caps["fills"] == baseline["fills"]
    assert with_caps["terminal_cash"] == baseline["terminal_cash"]


def test_limits_demo_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/risk_limits.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout and "max_symbol_gross_exceeded" in run.stdout
    assert "exposure_limit_breach" in run.stdout and list(tmp_path.iterdir()) == []
