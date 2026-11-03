"""Select a single entity's latest received record without reviving older state."""

from collections.abc import Iterable, Mapping

SOURCE_KINDS = ("SYNTHETIC_TEST_ONLY", "REAL_OBSERVED")


def _integer(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer in nanoseconds")


def select_asof(
    rows: Iterable[Mapping[str, object]],
    *,
    decision_ns: int,
    max_age_ns: int,
    source_kind: str,
    source: str,
    entity: str,
) -> dict[str, object]:
    """Return an available record, or None plus an explicit reason.

    Required metadata: record_id, source_kind, source, entity, timestamp,
    ingested_at, timestamp_basis ('exchange'/'received'), complete (bool).
    timestamp is event/state time, ingested_at is receipt time; all times are ns.
    Validate all rows, then choose latest receipt <= decision before checking
    event time, clock inversion, completeness and age. Never fall back to an
    older record. Provenance labels and complete are caller-supplied assertions,
    not verification of clock accuracy or account/position child rows.
    """
    _integer(decision_ns, "decision_ns")
    _integer(max_age_ns, "max_age_ns")
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind must be SYNTHETIC_TEST_ONLY or REAL_OBSERVED")
    for name, value in (("source", source), ("entity", entity)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")

    required = (
        "record_id", "source_kind", "source", "entity", "timestamp",
        "ingested_at", "timestamp_basis", "complete",
    )
    latest = None
    tied = False
    seen_ids = set()
    for row in rows:
        if not isinstance(row, Mapping) or any(key not in row for key in required):
            raise ValueError("Each record must contain all required metadata")
        record_id = row["record_id"]
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("record_id must be a nonempty string")
        if record_id in seen_ids:
            raise ValueError("Duplicate record_id; reconcile observations before selection")
        seen_ids.add(record_id)
        if (row["source_kind"], row["source"], row["entity"]) != (source_kind, source, entity):
            raise ValueError("Mixed source kind, source or entity")
        _integer(row["timestamp"], "timestamp")
        _integer(row["ingested_at"], "ingested_at")
        if row["timestamp_basis"] not in ("exchange", "received"):
            raise ValueError("timestamp_basis must be exchange or received")
        if row["timestamp_basis"] == "received" and row["timestamp"] != row["ingested_at"]:
            raise ValueError("Received-basis timestamp must equal ingested_at")
        if type(row["complete"]) is not bool:
            raise ValueError("complete must be bool")
        if row["ingested_at"] > decision_ns:
            continue
        if latest is None or row["ingested_at"] > latest["ingested_at"]:
            latest, tied = row, False
        elif row["ingested_at"] == latest["ingested_at"]:
            tied = True

    result = {
        "status": "unavailable", "reason": "no_available_record", "record": None,
        "record_id": None, "age_ns": None,
    }
    if latest is None:
        return result
    if tied:
        result["reason"] = "ambiguous_latest_receipt"
        return result
    age = max(decision_ns - latest["timestamp"], decision_ns - latest["ingested_at"])
    result.update(record_id=latest["record_id"], age_ns=age)
    if latest["timestamp"] > decision_ns:
        result["reason"] = "future_event"
    elif latest["timestamp"] > latest["ingested_at"]:
        result["reason"] = "clock_inversion"
    elif not latest["complete"]:
        result["reason"] = "incomplete_snapshot"
    elif age > max_age_ns:
        result["reason"] = "stale_snapshot"
    else:
        result.update(status="available", reason="available", record=dict(latest))
    return result
