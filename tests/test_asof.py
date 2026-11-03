from copy import deepcopy

import pytest

from market_microstructure import select_asof


def record(**changes):
    return {"record_id": "r1", "source_kind": "SYNTHETIC_TEST_ONLY",
            "source": "synthetic", "entity": "account-1/BTC", "timestamp": 90,
            "ingested_at": 95, "timestamp_basis": "exchange", "complete": True,
            "positions": [{"signed_size": "2", "liquidation_price": "99.9"}], **changes}


def select(rows, **changes):
    return select_asof(rows, **{"decision_ns": 100, "max_age_ns": 20,
                              "source_kind": "SYNTHETIC_TEST_ONLY", "source": "synthetic",
                              "entity": "account-1/BTC", **changes})


def test_latest_received_wins_in_unsorted_input_and_input_is_unchanged():
    rows = [record(record_id="new", timestamp=96, ingested_at=98), record()]
    before = deepcopy(rows)
    result = select(iter(rows))
    assert result["status"] == "available"
    assert result["record_id"] == "new" and result["age_ns"] == 4
    assert result["record"] == rows[0] and result["record"] is not rows[0]
    assert rows == before


def test_receipt_plus_one_ns_cannot_change_past_selection():
    baseline = [record()]
    assert select(baseline + [record(record_id="future", timestamp=99, ingested_at=101)]) == select(baseline)


def test_empty_input_and_future_receipts_are_unavailable_not_zero():
    for rows in ([], [record(ingested_at=101)]):
        result = select(rows)
        assert result["record"] is None
        assert result["reason"] == "no_available_record"


@pytest.mark.parametrize("changes,reason", [
    ({"timestamp": 101, "ingested_at": 99}, "future_event"),
    ({"timestamp": 99, "ingested_at": 98}, "clock_inversion"),
    ({"timestamp": 70, "ingested_at": 98}, "stale_snapshot"),
    ({"timestamp": 96, "ingested_at": 98, "complete": False}, "incomplete_snapshot"),
])
def test_bad_latest_state_never_revives_older_state(changes, reason):
    result = select([record(), record(record_id="new", **changes)])
    assert result["status"] == "unavailable" and result["record"] is None
    assert result["reason"] == reason and result["record_id"] == "new"


@pytest.mark.parametrize("timestamp,received,age,status", [
    (80, 99, 20, "available"), (79, 99, 21, "unavailable"),
    (95, 100, 5, "available"), (100, 100, 0, "available"),
])
def test_exact_age_and_receipt_boundaries(timestamp, received, age, status):
    result = select([record(timestamp=timestamp, ingested_at=received)])
    assert result["age_ns"] == age and result["status"] == status


def test_zero_max_age_only_accepts_same_instant():
    assert select([record(timestamp=100, ingested_at=100)], max_age_ns=0)["status"] == "available"
    assert select([record(timestamp=99, ingested_at=100)], max_age_ns=0)["reason"] == "stale_snapshot"


def test_received_basis_and_successful_empty_state_are_explicit():
    result = select([record(timestamp=95, ingested_at=95, timestamp_basis="received", positions=[])])
    assert result["status"] == "available"
    assert result["record"]["positions"] == []
    assert result["record"]["timestamp_basis"] == "received"


def test_large_epoch_ns_preserves_one_ns_boundary():
    decision = 1_800_000_000_000_000_000
    r = record(timestamp=decision - 1, ingested_at=decision)
    assert select([r], decision_ns=decision, max_age_ns=1)["status"] == "available"
    assert select([r], decision_ns=decision, max_age_ns=0)["reason"] == "stale_snapshot"


def test_equal_latest_receipts_are_ambiguous_in_either_order():
    a, b = record(), record(record_id="r2", timestamp=91)
    assert select([a, b]) == select([b, a])
    assert select([a, b])["reason"] == "ambiguous_latest_receipt"
    newer = record(record_id="r3", timestamp=96, ingested_at=98)
    assert select([a, b, newer])["record_id"] == "r3"


def test_duplicate_identity_is_an_error_even_when_future():
    with pytest.raises(ValueError):
        select([record(ingested_at=101), record(ingested_at=101)])


@pytest.mark.parametrize("changes", [{"source_kind": "REAL_OBSERVED"},
    {"source": "other_venue"}, {"entity": "account-2/BTC"}])
def test_mixed_provenance_is_rejected_even_for_future_rows(changes):
    with pytest.raises(ValueError):
        select([record(), record(record_id="future", ingested_at=101, **changes)])


def test_explicit_real_kind_is_not_automatically_relabelled():
    # Metadata arithmetic only: this is not real market data or UTC certification.
    r = record(source_kind="REAL_OBSERVED", source="declared_local_archive")
    result = select([r], source_kind="REAL_OBSERVED", source="declared_local_archive")
    assert result["record"]["source_kind"] == "REAL_OBSERVED"


@pytest.mark.parametrize("field", ["record_id", "source_kind", "source", "entity", "timestamp",
                                   "ingested_at", "timestamp_basis", "complete"])
def test_missing_required_metadata(field):
    r = record()
    del r[field]
    with pytest.raises(ValueError):
        select([r])


@pytest.mark.parametrize("field", ["timestamp", "ingested_at"])
@pytest.mark.parametrize("bad", [-1, True, 1.5, "95", None])
def test_invalid_record_timestamps(field, bad):
    with pytest.raises(ValueError):
        select([record(**{field: bad})])


@pytest.mark.parametrize("changes", [{"record_id": ""}, {"record_id": []},
    {"timestamp_basis": "unknown"}, {"timestamp_basis": "received"},
    {"complete": 1}, {"complete": None}])
def test_invalid_metadata(changes):
    with pytest.raises(ValueError):
        select([record(**changes)])


@pytest.mark.parametrize("changes", [{"decision_ns": -1}, {"decision_ns": True},
    {"decision_ns": 1.5}, {"max_age_ns": -1}, {"max_age_ns": "20"},
    {"max_age_ns": True}, {"source_kind": "unknown"}, {"source": ""}, {"entity": None}])
def test_invalid_selection_arguments(changes):
    with pytest.raises(ValueError):
        select([], **changes)


@pytest.mark.parametrize("bad", [None, [], "not a record"])
def test_non_mapping_record(bad):
    with pytest.raises(ValueError):
        select([bad])
