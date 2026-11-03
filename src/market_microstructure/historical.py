"""Single-asset historical exposure replay. No orders, labels, or price forecasts."""

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, localcontext

from .asof import select_asof
from .gravity import gravity, liquidation_imbalance


def _decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing or invalid decimal")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not math.isfinite(float(number)):
            raise ValueError("Nonfinite decimal")
        return number
    except (InvalidOperation, OverflowError) as error:
        raise ValueError("Invalid decimal") from error


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be nonnegative integer ns/count")


def validate_policy(policy):
    for key in ("start_ns", "end_ns", "step_ns", "snapshot_age_ns", "market_age_ns"):
        _integer(policy[key], key)
    if policy["end_ns"] < policy["start_ns"] or policy["step_ns"] == 0:
        raise ValueError("Invalid decision range or step")
    if policy["asset"] != "BTC":
        raise ValueError("This Phase 2 runner is restricted to BTC")
    if policy["source_kind"] not in ("REAL_OBSERVED", "SYNTHETIC_TEST_ONLY"):
        raise ValueError("Explicit source kind required")
    for key in ("source", "session_id"):
        if not isinstance(policy[key], str) or not policy[key].strip():
            raise ValueError(f"Nonempty {key} required")
    gravity([], policy["tau_bps"])
    liquidation_imbalance(0, 0, policy["epsilon"])


def _validate_rows(data, policy):
    for kind in ("accounts", "positions", "contexts", "bbo"):
        for row in data[kind]:
            if (row["source_kind"], row["source"]) != (policy["source_kind"], policy["source"]):
                raise ValueError("Mixed data provenance")
            _integer(row["timestamp"], "timestamp")
            _integer(row["ingested_at"], "ingested_at")
            if row["timestamp_basis"] not in ("exchange", "received"):
                raise ValueError("Unknown timestamp basis")
            if row["timestamp_basis"] == "received" and row["timestamp"] != row["ingested_at"]:
                raise ValueError("Received-basis timestamp mismatch")
            if kind in ("contexts", "bbo") and row["coin"] != policy["asset"]:
                raise ValueError("Mixed market asset")
            if kind in ("accounts", "positions"):
                if not isinstance(row["wallet"], str) or not row["wallet"]:
                    raise ValueError("Account identifier required")
            if kind == "accounts" and "requested_at" in row:
                _integer(row["requested_at"], "requested_at")


def _snapshot_reason(header, children, decision):
    visible = [p for p in children if p["ingested_at"] <= decision]
    if header.get("requested_at", header["ingested_at"]) > header["ingested_at"]:
        return "request_after_receipt", visible
    expected = header["scoped_position_count"]
    if type(expected) is not int or expected < 0:
        return "invalid_position_count", visible
    if len(visible) != expected:
        return "incomplete_snapshot", visible
    if len({p["coin"] for p in visible}) != len(visible):
        return "duplicate_position_coin", visible
    if any(p["wallet"] != header["wallet"] for p in visible):
        return "child_wallet_mismatch", visible
    if any(p["timestamp"] > decision for p in visible):
        return "future_child_event", visible
    if any((p["timestamp"], p["ingested_at"]) !=
           (header["timestamp"], header["ingested_at"]) for p in visible):
        return "child_time_mismatch", visible
    return None, visible


def _metadata(row, policy, entity, record_id, complete=True):
    return {"record_id": record_id, "source_kind": policy["source_kind"],
            "source": policy["source"], "entity": entity, "timestamp": row["timestamp"],
            "ingested_at": row["ingested_at"], "timestamp_basis": row["timestamp_basis"],
            "complete": complete}


def _market_at(timeline, decision, policy):
    rows, times, max_events = timeline
    index = bisect_right(times, decision) - 1
    if index < 0:
        return None, "no_available_record"
    first = bisect_left(times, times[index])
    candidates = rows[first:index + 1]
    selected = select_asof(
        [_metadata(r, policy, policy["asset"], r["event_id"]) for r in candidates],
        decision_ns=decision, max_age_ns=policy["market_age_ns"],
        source_kind=policy["source_kind"], source=policy["source"], entity=policy["asset"],
    )
    if selected["status"] != "available":
        return None, selected["reason"]
    row = rows[index]
    if row["session_id"] != policy["session_id"]:
        return None, "session_changed"
    if row["timestamp"] < max_events[index]:
        return None, "out_of_order_market_event"
    return row, None


def _timeline(rows):
    if len({r["event_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate market event ID")
    ordered = sorted(rows, key=lambda r: (r["ingested_at"], r["event_id"]))
    maximum, prefix = -1, []
    for row in ordered:
        maximum = max(maximum, row["timestamp"])
        prefix.append(maximum)
    return ordered, [r["ingested_at"] for r in ordered], prefix


def _exposure(position, reference):
    """Decimal preserves the source definition's price-side and distance boundaries."""
    try:
        size = _decimal(position["signed_size"])
    except ValueError:
        return "invalid_position_size", None, None, None
    if size == 0:
        return "zero_position", None, Decimal(0), None
    notional = abs(size) * reference if reference is not None else None
    if notional is not None and not math.isfinite(float(notional)):
        return "notional_overflow", None, None, None
    if position["liquidation_px"] is None:
        return "missing_liquidation_price", None, notional, None
    try:
        threshold = _decimal(position["liquidation_px"])
        if threshold <= 0:
            raise ValueError("Nonpositive threshold")
    except ValueError:
        return "invalid_liquidation_price", None, notional, None
    if reference is None:
        return "reference_unavailable", None, None, None
    if threshold == reference:
        return "at_reference_price", None, notional, None
    if (size > 0 and threshold > reference) or (size < 0 and threshold < reference):
        return "wrong_side_of_reference", None, notional, None
    distance = abs(threshold - reference) * 10000 / reference
    if not math.isfinite(float(distance)):
        return "distance_overflow", None, notional, None
    return None, "down" if size > 0 else "up", notional, float(distance)


def replay_history(data, policy):
    """Replay normalized in-memory rows; outputs contain aggregates, no account IDs.

    Account membership starts only when a header has been received. Children
    arriving later cannot change an earlier decision's completeness. All asset
    children are needed for integrity; only BTC contributes to exposure.
    """
    validate_policy(policy)
    _validate_rows(data, policy)
    accounts = sorted(data["accounts"], key=lambda r: (r["ingested_at"], r["snapshot_id"]))
    if len({h["snapshot_id"] for h in accounts}) != len(accounts):
        raise ValueError("Duplicate account snapshot ID")
    headers = {h["snapshot_id"]: h for h in accounts}
    children = defaultdict(list)
    for p in data["positions"]:
        children[p["snapshot_id"]].append(p)
    integrity = Counter({k: 0 for k in ("incomplete_snapshot", "duplicate_position_coin",
        "child_wallet_mismatch", "future_child_event", "child_time_mismatch",
        "invalid_position_count", "request_after_receipt")})
    integrity["orphan_position_rows"] = sum(p["snapshot_id"] not in headers for p in data["positions"])
    for h in accounts:
        reason, _ = _snapshot_reason(h, children[h["snapshot_id"]], 2**100)
        if reason:
            integrity[reason] += 1
    context_timeline, bbo_timeline = _timeline(data["contexts"]), _timeline(data["bbo"])
    latest, cursor, series = {}, 0, []
    used_accounts, used_snapshots, used_positions, selected_snapshots = set(), set(), set(), set()
    excluded_snapshots, snapshot_checks = defaultdict(set), Counter()
    included_position_ids, excluded_position_ids, position_checks = set(), defaultdict(set), Counter()
    with localcontext() as ctx:
        ctx.prec = 50
        for decision in range(policy["start_ns"], policy["end_ns"] + 1, policy["step_ns"]):
            while cursor < len(accounts) and accounts[cursor]["ingested_at"] <= decision:
                h = accounts[cursor]
                prior = latest.get(h["wallet"], [])
                if prior and prior[0]["ingested_at"] == h["ingested_at"]:
                    prior.append(h)
                else:
                    latest[h["wallet"]] = [h]
                cursor += 1
            reasons, snapshot_reasons, position_reasons = [], Counter(), Counter()
            feature_receipts, feature_events = [], []
            context, problem = _market_at(context_timeline, decision, policy)
            if problem:
                reasons.append("context_" + problem)
            book, problem = _market_at(bbo_timeline, decision, policy)
            if problem:
                reasons.append("bbo_" + problem)
            for market in (context, book):
                if market is not None:
                    feature_receipts.append(market["ingested_at"])
                    feature_events.append(market["timestamp"])
            reference, mark, oi = None, None, None
            if context:
                try:
                    reference, mark, oi = (_decimal(context[k]) for k in ("mid_px", "mark_px", "open_interest"))
                    if reference <= 0 or mark <= 0 or oi < 0:
                        raise ValueError("Invalid context")
                except ValueError:
                    reasons.append("invalid_context"); reference = None
            if book:
                try:
                    bid, ask, bs, az = (_decimal(book[k]) for k in ("bid_px", "ask_px", "bid_sz", "ask_sz"))
                    if not 0 < bid <= ask or min(bs, az) < 0 or bs + az <= 0:
                        raise ValueError("Invalid book")
                except ValueError:
                    reasons.append("invalid_bbo")
            good_accounts, positions = 0, []
            for wallet, candidates in latest.items():
                checks = [_snapshot_reason(h, children[h["snapshot_id"]], decision) for h in candidates]
                selected = select_asof(
                    [_metadata(h, policy, wallet, h["snapshot_id"], checks[i][0] is None)
                     for i, h in enumerate(candidates)], decision_ns=decision,
                    max_age_ns=policy["snapshot_age_ns"], source_kind=policy["source_kind"],
                    source=policy["source"], entity=wallet,
                )
                selected_snapshots.update(h["snapshot_id"] for h in candidates)
                if selected["status"] != "available":
                    reason = selected["reason"]
                    if reason == "incomplete_snapshot":
                        reason = checks[0][0]
                    snapshot_reasons[reason] += 1; snapshot_checks[reason] += 1
                    excluded_snapshots[reason].update(h["snapshot_id"] for h in candidates)
                    continue
                h = candidates[0]; good_accounts += 1; snapshot_checks["available"] += 1
                feature_receipts.append(h["ingested_at"]); feature_events.append(h["timestamp"])
                used_accounts.add(wallet); used_snapshots.add(h["snapshot_id"])
                for p in checks[0][1]:
                    if p["coin"] == policy["asset"]:
                        positions.append(p); used_positions.add((p["snapshot_id"], p["coin"]))
            if not good_accounts:
                reasons.append("no_fresh_complete_accounts")
            pairs = {"up": [], "down": []}; known, valid, unknown = [], [], 0
            for p in positions:
                problem, side, notional, distance = _exposure(p, reference)
                if notional is not None:
                    known.append(notional)
                else:
                    unknown += 1
                if problem:
                    position_reasons[problem] += 1
                    position_checks[problem] += 1
                    excluded_position_ids[problem].add((p["snapshot_id"], p["coin"]))
                else:
                    pairs[side].append((float(notional), distance)); valid.append(notional)
                    position_checks["included"] += 1
                    included_position_ids.add((p["snapshot_id"], p["coin"]))
            if positions and not valid:
                reasons.append("no_valid_liquidation_threshold")
            known_total, valid_total = sum(known, Decimal(0)), sum(valid, Decimal(0))
            totals_finite = math.isfinite(float(known_total)) and math.isfinite(float(valid_total))
            if not totals_finite:
                reasons.append("exposure_sum_overflow")
            deviation = proxy = None
            if reference is not None:
                deviation = float((reference / mark - 1) * 10000)
                proxy = float(known_total / (oi * mark)) if oi > 0 and not unknown else None
                if not math.isfinite(deviation) or (proxy is not None and not math.isfinite(proxy)):
                    reasons.append("context_ratio_overflow"); deviation = proxy = None
            up = down = lgi = None
            if not reasons:
                try:
                    up, down = (gravity(pairs[s], policy["tau_bps"]) for s in ("up", "down"))
                    lgi = liquidation_imbalance(up, down, policy["epsilon"])
                except ValueError:
                    reasons.append("gravity_numeric_error"); up = down = lgi = None
            series.append({
                "decision_ns": decision, "status": "unavailable" if reasons else "available",
                "reasons": reasons, "known_accounts": len(latest), "valid_accounts": good_accounts,
                "excluded_accounts": sum(snapshot_reasons.values()), "snapshot_exclusions": dict(snapshot_reasons),
                "btc_positions": len(positions), "included_positions": len(valid),
                "excluded_positions": sum(position_reasons.values()), "position_exclusions": dict(position_reasons),
                "unknown_notional_positions": unknown,
                "known_position_notional": float(known_total) if reference is not None and totals_finite else None,
                "included_notional": float(valid_total) if reference is not None and totals_finite else None,
                "valid_threshold_notional_fraction": float(valid_total / known_total) if known_total > 0 and not unknown and totals_finite else None,
                "reference_mid": float(reference) if reference is not None else None,
                "mark_mid_deviation_bps": deviation, "exposure_oi_proxy": proxy,
                "context_received_ns": context["ingested_at"] if context else None,
                "context_event_ns": context["timestamp"] if context else None,
                "bbo_received_ns": book["ingested_at"] if book else None,
                "bbo_event_ns": book["timestamp"] if book else None,
                "feature_max_received_ns": max(feature_receipts, default=None),
                "feature_max_event_ns": max(feature_events, default=None),
                "gravity_up": up, "gravity_down": down, "lgi": lgi,
            })
    eligible = [r for r in series if r["status"] == "available"]
    point_exclusions = Counter(reason for r in series for reason in r["reasons"])
    statistics_out = {}
    for key in ("gravity_up", "gravity_down", "lgi"):
        values = [r[key] for r in eligible]
        statistics_out[key] = {"min": min(values), "max": max(values), "mean": statistics.mean(values),
                               "first": values[0], "last": values[-1]} if values else None
    intervals = []
    for r in series:
        if r["status"] == "unavailable":
            if not intervals or intervals[-1]["last_decision_ns"] + policy["step_ns"] != r["decision_ns"]:
                intervals.append({"first_decision_ns": r["decision_ns"], "last_decision_ns": r["decision_ns"]})
            else:
                intervals[-1]["last_decision_ns"] = r["decision_ns"]
    return {"policy": dict(policy), "input_counts": {k: len(data[k]) for k in data},
            "integrity": dict(integrity), "summary": {
                "decision_points": len(series), "eligible_points": len(eligible),
                "excluded_points": len(series) - len(eligible), "point_exclusion_reasons": dict(point_exclusions),
                "used_unique_accounts": len(used_accounts), "selected_unique_snapshots": len(selected_snapshots),
                "used_unique_snapshots": len(used_snapshots), "used_unique_btc_positions": len(used_positions),
                "input_unique_accounts": len({h["wallet"] for h in accounts}),
                "input_positions_by_coin": dict(Counter(p["coin"] for p in data["positions"])),
                "unique_included_btc_positions": len(included_position_ids),
                "position_evaluations": dict(position_checks),
                "unique_position_exclusions": {k: len(v) for k, v in excluded_position_ids.items()},
                "snapshot_evaluations": dict(snapshot_checks),
                "unique_snapshot_exclusions": {k: len(v) for k, v in excluded_snapshots.items()},
                "unavailable_decision_intervals": intervals, "statistics": statistics_out,
            }, "series": series}
