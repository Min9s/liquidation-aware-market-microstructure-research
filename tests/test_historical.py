import json
from copy import deepcopy

import pytest

from examples.historical_synthetic import synthetic_inputs
from market_microstructure.historical import replay_history


def test_golden_replay_and_all_asset_children_are_checked():
    data, policy = synthetic_inputs()
    before = deepcopy(data)
    r = replay_history(data, policy)
    assert r["summary"]["eligible_points"] == 3
    assert r["summary"]["used_unique_accounts"] == 2
    assert r["summary"]["used_unique_btc_positions"] == 2
    for row in r["series"]:
        assert row["gravity_down"] == pytest.approx(73.57588823428847)
        assert row["gravity_up"] == pytest.approx(24.62549958716964)
        assert row["lgi"] == pytest.approx(-0.49846941813201256)
        assert row["known_position_notional"] == 500
    assert data == before
    assert "synthetic-long" not in json.dumps(r)


def test_future_account_membership_cannot_change_earlier_counts_or_features():
    data, policy = synthetic_inputs()
    baseline = replay_history(data, policy)
    h = dict(data["accounts"][0], snapshot_id="future", wallet="future-account",
             timestamp=policy["start_ns"] + 31 * 10**9, ingested_at=policy["start_ns"] + 32 * 10**9,
             scoped_position_count=0)
    data["accounts"].append(h)
    changed = replay_history(data, policy)
    assert baseline["series"][:2] == changed["series"][:2]
    assert changed["series"][2]["known_accounts"] == 3


def test_future_child_does_not_rewrite_earlier_completeness_reason():
    data, policy = synthetic_inputs()
    child = data["positions"].pop(1)
    baseline = replay_history(data, policy)
    child["ingested_at"] = policy["start_ns"] + 31 * 10**9
    data["positions"].append(child)
    result = replay_history(data, policy)
    assert baseline["series"][:2] == result["series"][:2]
    assert result["series"][2]["snapshot_exclusions"] == {"child_time_mismatch": 1}


def test_latest_incomplete_snapshot_does_not_revive_old_position():
    data, policy = synthetic_inputs()
    h = dict(data["accounts"][0], snapshot_id="incomplete", timestamp=policy["start_ns"], ingested_at=policy["start_ns"])
    data["accounts"].append(h)
    r = replay_history(data, policy)
    assert r["series"][0]["snapshot_exclusions"] == {"incomplete_snapshot": 1}
    assert r["series"][0]["gravity_down"] == 0


def test_empty_success_closes_previous_position():
    data, policy = synthetic_inputs()
    data["accounts"].append(dict(data["accounts"][0], snapshot_id="flat", scoped_position_count=0,
                                  timestamp=policy["start_ns"], ingested_at=policy["start_ns"]))
    r = replay_history(data, policy)
    assert r["series"][0]["btc_positions"] == 1
    assert r["series"][0]["gravity_down"] == 0


@pytest.mark.parametrize("mutation,reason", [
    ("wallet", "child_wallet_mismatch"), ("timestamp", "child_time_mismatch"),
    ("missing", "incomplete_snapshot"), ("duplicate", "duplicate_position_coin"),
    ("request", "request_after_receipt"),
])
def test_snapshot_integrity_exclusions(mutation, reason):
    data, policy = synthetic_inputs()
    if mutation == "wallet": data["positions"][0]["wallet"] = "other"
    if mutation == "timestamp": data["positions"][0]["timestamp"] -= 1
    if mutation == "missing": data["positions"].pop(1)
    if mutation == "duplicate": data["positions"][1]["coin"] = "BTC"
    if mutation == "request": data["accounts"][0]["requested_at"] = policy["start_ns"]
    row = replay_history(data, policy)["series"][0]
    assert row["snapshot_exclusions"] == {reason: 1}


def test_stale_snapshot_boundary_and_no_fabricated_gravity():
    data, policy = synthetic_inputs()
    policy["snapshot_age_ns"] = 32 * 10**9
    r = replay_history(data, policy)
    assert r["series"][1]["status"] == "available"
    assert r["series"][2]["snapshot_exclusions"] == {"stale_snapshot": 2}
    assert r["series"][2]["lgi"] is None


def test_market_gap_is_not_filled_and_unavailable_interval_is_reported():
    data, policy = synthetic_inputs()
    data["contexts"].pop(1)
    r = replay_history(data, policy)
    assert r["summary"]["excluded_points"] == 1
    assert r["series"][1]["gravity_up"] is None
    assert "context_stale_snapshot" in r["series"][1]["reasons"]
    assert r["summary"]["unavailable_decision_intervals"][0]["first_decision_ns"] == policy["start_ns"] + 30 * 10**9


@pytest.mark.parametrize("kind", ["contexts", "bbo"])
def test_future_market_receive_cannot_enter_decision(kind):
    data, policy = synthetic_inputs()
    data[kind][0]["ingested_at"] += 1
    data[kind][0]["timestamp_basis"] = "exchange"
    row = replay_history(data, policy)["series"][0]
    assert row["status"] == "unavailable"
    assert ("context_" if kind == "contexts" else "bbo_") + "no_available_record" in row["reasons"]


def test_session_boundary_and_clock_inversion_are_not_hidden():
    data, policy = synthetic_inputs()
    data["bbo"][1]["session_id"] = "s2"
    data["bbo"][2]["timestamp_basis"] = "exchange"
    data["bbo"][2]["timestamp"] += 1
    rows = replay_history(data, policy)["series"]
    assert "bbo_session_changed" in rows[1]["reasons"]
    assert "bbo_future_event" in rows[2]["reasons"]


def test_delayed_old_book_does_not_replace_newer_event_state():
    data, policy = synthetic_inputs()
    delayed = dict(data["bbo"][0], event_id="delayed", timestamp_basis="exchange",
                   timestamp=policy["start_ns"] - 1, ingested_at=policy["start_ns"] + 1)
    data["bbo"].append(delayed)
    policy["step_ns"] = 1; policy["end_ns"] = policy["start_ns"] + 1
    assert "bbo_out_of_order_market_event" in replay_history(data, policy)["series"][1]["reasons"]


def test_missing_thresholds_preserve_notional_but_do_not_create_zero_lgi():
    data, policy = synthetic_inputs()
    for p in data["positions"]: p["liquidation_px"] = None
    row = replay_history(data, policy)["series"][0]
    assert row["position_exclusions"] == {"missing_liquidation_price": 2}
    assert row["known_position_notional"] == 500
    assert row["lgi"] is None


def test_decimal_price_boundary_matches_source_definition():
    data, policy = synthetic_inputs()
    data["positions"][0]["liquidation_px"] = "99.999999999999999999"
    row = replay_history(data, policy)["series"][0]
    assert row["included_positions"] == 2
    assert row["gravity_down"] == pytest.approx(200)


@pytest.mark.parametrize("field,value,reason", [
    ("signed_size", "bad", "invalid_position_size"),
    ("liquidation_px", "NaN", "invalid_liquidation_price"),
    ("liquidation_px", "101", "wrong_side_of_reference"),
    ("liquidation_px", "100", "at_reference_price"),
])
def test_position_exclusion_reasons(field, value, reason):
    data, policy = synthetic_inputs(); data["positions"][0][field] = value
    assert replay_history(data, policy)["series"][0]["position_exclusions"] == {reason: 1}


@pytest.mark.parametrize("kind", ["accounts", "positions", "contexts", "bbo"])
def test_mixed_source_kind_is_rejected(kind):
    data, policy = synthetic_inputs(); data[kind][0]["source_kind"] = "REAL_OBSERVED"
    with pytest.raises(ValueError, match="provenance"):
        replay_history(data, policy)


def test_duplicate_headers_are_not_arbitrarily_selected():
    data, policy = synthetic_inputs(); data["accounts"].append(dict(data["accounts"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        replay_history(data, policy)


def test_input_order_does_not_change_replay():
    data, policy = synthetic_inputs(); expected = replay_history(data, policy)
    for rows in data.values(): rows.reverse()
    assert replay_history(data, policy) == expected


def test_position_and_snapshot_repeated_counts_are_explicit():
    data, policy = synthetic_inputs()
    data["positions"][0]["liquidation_px"] = None
    summary = replay_history(data, policy)["summary"]
    assert summary["position_evaluations"] == {"missing_liquidation_price": 3, "included": 3}
    assert summary["unique_position_exclusions"] == {"missing_liquidation_price": 1}
    assert summary["unique_included_btc_positions"] == 1
    assert summary["input_positions_by_coin"] == {"BTC": 2, "ETH": 1}


def test_orphan_position_is_recorded_without_joining_an_unrelated_account():
    data, policy = synthetic_inputs()
    data["positions"].append(dict(data["positions"][0], snapshot_id="no-parent"))
    result = replay_history(data, policy)
    assert result["integrity"]["orphan_position_rows"] == 1
    assert result["series"][0]["btc_positions"] == 2


def test_known_empty_observed_state_is_distinct_from_unknown_thresholds():
    data, policy = synthetic_inputs()
    for h in data["accounts"]: h["scoped_position_count"] = 0
    data["positions"] = []
    r = replay_history(data, policy)
    assert r["series"][0]["status"] == "available"
    assert r["series"][0]["lgi"] == 0


def test_invalid_requested_timestamp_is_rejected():
    data, policy = synthetic_inputs(); data["accounts"][0]["requested_at"] = True
    with pytest.raises(ValueError, match="requested_at"):
        replay_history(data, policy)


def test_feature_lineage_never_exceeds_decision_time():
    data, policy = synthetic_inputs()
    for row in replay_history(data, policy)["series"]:
        assert row["feature_max_event_ns"] <= row["decision_ns"]
        assert row["feature_max_received_ns"] <= row["decision_ns"]
        assert row["bbo_event_ns"] == row["bbo_received_ns"]


def test_account_clock_inversion_is_excluded_without_fallback():
    data, policy = synthetic_inputs()
    for h in data["accounts"]:
        h["timestamp"] = h["ingested_at"] + 1
    for p in data["positions"]:
        p["timestamp"] = p["ingested_at"] + 1
    row = replay_history(data, policy)["series"][0]
    assert row["snapshot_exclusions"] == {"clock_inversion": 2}
    assert row["lgi"] is None
