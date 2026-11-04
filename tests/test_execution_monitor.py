from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, localcontext
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.execution_walkthrough import NS, synthetic_inputs
from examples.execution_scenarios import scenario_inputs
from market_microstructure.execution import replay_execution
from market_microstructure.execution_monitor import monitor_execution


def drawdown_inputs():
    bundle = synthetic_inputs()
    bundle["policy"].update(initial_cash="204", fee_bps="0")
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"][1].update(bids=[["49", "10"]], asks=[["51", "10"]])
    recovery = deepcopy(bundle["quotes"][0])
    recovery.update(quote_id="q4", timestamp_ns=4 * NS, available_ns=4 * NS)
    bundle["quotes"].append(recovery)
    return bundle


def test_transient_drawdown_records_hand_computed_deficit_and_recovery():
    result = monitor_execution(drawdown_inputs())
    started, resolved = result["risk_events"]
    assert (started["change"], started["timestamp_ns"], started["to_status"]) == (
        "started", 3 * NS, "collateral_breach")
    account = started["account"]
    assert Decimal(account["cash"]) == 204
    assert Decimal(account["equity"]) == 102
    assert Decimal(account["reserved_margin"]) == 202
    assert Decimal(account["collateral_headroom"]) == -100
    assert resolved["change"] == "resolved" and resolved["timestamp_ns"] == 4 * NS
    assert Decimal(resolved["account"]["collateral_headroom"]) == 0
    assert result["episodes"][0]["observed_duration_ns"] == NS
    assert result["episodes"][0]["open_at_end"] is False
    assert result["terminal_status"] == "healthy" and not result["automatic_actions"]


def stale_inputs():
    bundle = synthetic_inputs()
    bundle["policy"].update(fee_bps="0", max_quote_age_ns=NS)
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"] = [bundle["quotes"][0]]
    quote = deepcopy(bundle["quotes"][0])
    quote.update(quote_id="q4", timestamp_ns=4 * NS, available_ns=4 * NS)
    bundle["quotes"].append(quote)
    return bundle


def test_stale_starts_one_ns_after_inclusive_age_and_ends_on_receipt():
    result = monitor_execution(stale_inputs())
    started, resolved = result["risk_events"]
    assert started["timestamp_ns"] == NS + 1
    assert started["trigger"]["kind"] == "quote_expiry"
    assert started["account"]["equity"] is None
    assert started["account"]["collateral_headroom"] is None
    assert started["account"]["marks"]["SYNTH-BTC-USDT"]["reason"] == "stale_quote"
    assert resolved["timestamp_ns"] == 4 * NS
    assert result["episodes"][0]["observed_duration_ns"] == 3 * NS - 1
    at_entry = [o for o in result["observations"] if o["trigger"]["kind"] == "fill"][0]
    assert at_entry["timestamp_ns"] == NS and at_entry["status"] == "healthy"


def test_replacement_quote_at_old_expiry_prevents_false_stale_transition():
    bundle = stale_inputs()
    bundle["quotes"][1].update(timestamp_ns=NS + 1, available_ns=NS + 1)
    bundle["policy"]["end_ns"] = 2 * NS
    result = monitor_execution(bundle)
    assert not result["risk_events"]
    assert not any(o["trigger"]["kind"] == "quote_expiry" for o in result["observations"])


def test_ignored_late_quote_and_its_expiry_cannot_overwrite_fresh_book():
    bundle = synthetic_inputs()
    bundle["policy"]["max_quote_age_ns"] = 3 * NS
    bundle["orders"] = [bundle["orders"][0]]
    late = deepcopy(bundle["quotes"][0])
    late.update(quote_id="late", timestamp_ns=NS, available_ns=4 * NS,
                bids=[["1", "10"]], asks=[["3", "10"]])
    bundle["quotes"].append(late)
    result = monitor_execution(bundle)
    assert not any(o["trigger"]["identity"] == "late" for o in result["observations"])
    assert result["terminal_status"] == "healthy"


def test_unresolved_episode_is_censored_at_end_not_marked_recovered():
    bundle = drawdown_inputs()
    bundle["quotes"].pop()
    result = monitor_execution(bundle)
    assert len(result["risk_events"]) == 1
    episode = result["episodes"][0]
    assert episode["end_ns"] is None and episode["open_at_end"]
    assert episode["observed_until_ns"] == 5 * NS
    assert episode["observed_duration_ns"] == 2 * NS


def test_breach_then_unavailable_then_recovery_is_one_episode_with_change():
    bundle = drawdown_inputs()
    invalid = deepcopy(bundle["quotes"][1])
    invalid.update(quote_id="invalid", timestamp_ns=3 * NS + 1,
                   available_ns=3 * NS + 1, valid=False, bids=[], asks=[])
    bundle["quotes"].append(invalid)
    result = monitor_execution(bundle)
    assert [e["change"] for e in result["risk_events"]] == ["started", "changed", "resolved"]
    assert result["risk_events"][1]["to_status"] == "valuation_unavailable"
    assert result["risk_events"][1]["account"]["equity"] is None
    assert len(result["episodes"]) == 1


def test_same_timestamp_bad_quote_and_close_keeps_zero_duration_risk_event():
    bundle = drawdown_inputs()
    close = synthetic_inputs()["orders"][1]
    close.update(decision_ns=2 * NS, signal_available_ns=2 * NS)
    bundle["orders"].append(close)
    result = monitor_execution(bundle)
    started, resolved = result["risk_events"]
    assert started["trigger"]["kind"] == "quote"
    assert resolved["trigger"]["kind"] == "fill"
    assert started["timestamp_ns"] == resolved["timestamp_ns"] == 3 * NS
    assert result["episodes"][0]["observed_duration_ns"] == 0
    assert result["execution"]["terminal_flat"]


def test_future_price_changes_do_not_change_past_observations():
    bundle = drawdown_inputs()
    original = monitor_execution(bundle)
    bundle["quotes"][-1].update(bids=[["69", "10"]], asks=[["71", "10"]])
    changed = monitor_execution(bundle)
    assert [o for o in changed["observations"] if o["timestamp_ns"] < 4 * NS] == [
        o for o in original["observations"] if o["timestamp_ns"] < 4 * NS]


def test_observer_never_changes_execution_or_input_for_all_public_scenarios():
    for bundle in scenario_inputs().values():
        before = deepcopy(bundle)
        expected = replay_execution(bundle)
        observed = monitor_execution(bundle)
        assert observed["execution"] == expected
        assert bundle == before
        assert observed["observations"][-1]["account"] == expected["terminal_account"]


def test_empty_portfolio_never_becomes_unavailable_when_unheld_quotes_expire():
    bundle = stale_inputs()
    bundle["orders"] = []
    result = monitor_execution(bundle)
    assert result["episodes"] == [] and result["terminal_status"] == "healthy"


def test_decimal_settings_are_local():
    bundle = drawdown_inputs()
    expected = monitor_execution(bundle)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        assert monitor_execution(bundle) == expected
        assert context.prec == 6 and context.rounding == ROUND_DOWN


def test_unavailable_reason_change_is_recorded_without_false_recovery():
    bundle = drawdown_inputs()
    invalid = deepcopy(bundle["quotes"][0])
    invalid.update(quote_id="invalid2", timestamp_ns=2 * NS, available_ns=2 * NS,
                   valid=False, bids=[], asks=[])
    bundle["quotes"].append(invalid)
    bundle["quotes"][1]["session_id"] = "new_session"
    result = monitor_execution(bundle)
    started, changed, resolved = result["risk_events"]
    assert [e["change"] for e in result["risk_events"]] == ["started", "changed", "resolved"]
    symbol = "SYNTH-BTC-USDT"
    assert started["account"]["marks"][symbol]["reason"] == "invalid_quote"
    assert changed["from_status"] == changed["to_status"] == "valuation_unavailable"
    assert changed["account"]["marks"][symbol]["reason"] == "session_changed"
    assert resolved["timestamp_ns"] == 4 * NS
    assert len(result["episodes"]) == 1


@pytest.mark.parametrize("end,expected", [(NS, "healthy"), (NS + 1, "valuation_unavailable")])
def test_expiry_at_horizon_is_included_but_not_invented_before_boundary(end, expected):
    bundle = stale_inputs()
    bundle["quotes"].pop()
    bundle["policy"]["end_ns"] = end
    result = monitor_execution(bundle)
    assert result["terminal_status"] == expected
    if result["episodes"]:
        assert result["episodes"][0]["observed_duration_ns"] == 0
        assert result["episodes"][0]["end_ns"] is None


def test_monitoring_demo_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/risk_monitoring.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "-100" in run.stdout and "1.000000001" in run.stdout
    assert "resolved" in run.stdout and list(tmp_path.iterdir()) == []
