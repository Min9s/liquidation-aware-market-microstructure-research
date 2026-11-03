"""Exploratory sampled-price outcomes. No fitting, trading, or IID inference."""

import math
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, localcontext

from .historical import _decimal, _market_at, _timeline, validate_policy
from .orderbook import bid_ask_spread, order_book_imbalance

LABELS = ("upper_first", "lower_first", "neither")
BIN_EDGES = [-1, -0.5, 0, 0.5, 1]


def validate_rules(rules):
    horizons = rules["horizons_seconds"]
    if not horizons or len(set(horizons)) != len(horizons):
        raise ValueError("Distinct positive horizons required")
    if any(type(h) is not int or h <= 0 for h in horizons):
        raise ValueError("Horizons must be positive integer seconds")
    if type(rules["max_gap_ns"]) is not int or rules["max_gap_ns"] <= 0:
        raise ValueError("Positive integer price gap required")
    width = _decimal(rules["barrier_bps"])
    if not 0 < width < 10000 or rules["bin_edges"] != BIN_EDGES:
        raise ValueError("Positive barrier below 10000bps and registered fixed bins required")


def observed_outcome(prices, *, decision_ns, horizon_seconds, reference_price, policy, rules):
    """Observe (decision, decision+horizon] using receipt times and a full path.

    End valuation uses the last strictly future observed mid at/before the end,
    within max_gap_ns. Both first hits at the same receipt are ambiguous. A
    conflicting final receipt group cannot supply an arbitrary terminal price.
    """
    validate_rules(rules)
    if type(decision_ns) is not int or decision_ns < 0 or type(horizon_seconds) is not int or horizon_seconds <= 0:
        raise ValueError("Nonnegative decision ns and positive integer horizon required")
    end = decision_ns + horizon_seconds * 10**9
    result = {"label": None, "label_status": "censored", "reason": "observation_end",
              "label_end_ns": end, "upper_hit": None, "lower_hit": None,
              "first_upper_ns": None, "first_lower_ns": None,
              "terminal_received_ns": None, "terminal_age_ns": None,
              "terminal_mid": None, "future_return_bps": None}
    if end > policy["end_ns"]:
        return result
    reference = _decimal(reference_price)
    if reference <= 0:
        raise ValueError("Positive reference price required")
    with localcontext() as ctx:
        ctx.prec = 50
        width = _decimal(rules["barrier_bps"]) / 10000
        upper, lower = reference * (1 + width), reference * (1 - width)
        result.update(upper_barrier=float(upper), lower_barrier=float(lower))
        path = sorted((r for r in prices if decision_ns < r["ingested_at"] <= end),
                      key=lambda r: (r["ingested_at"], r["event_id"]))
        if not path:
            result["reason"] = "no_future_price"
            return result
        groups, previous, seen = defaultdict(list), decision_ns, set()
        for row in path:
            received, event = row["ingested_at"], row["timestamp"]
            problem = None
            if type(received) is not int or type(event) is not int or min(received, event) < 0:
                problem = "invalid_price_timestamp"
            elif (row["source_kind"], row["source"], row["coin"]) != (policy["source_kind"], policy["source"], policy["asset"]):
                problem = "price_provenance_mismatch"
            elif row["session_id"] != policy["session_id"]:
                problem = "session_changed"
            elif row["timestamp_basis"] not in ("exchange", "received"):
                problem = "unknown_price_time_basis"
            elif row["timestamp_basis"] == "received" and event != received:
                problem = "received_basis_mismatch"
            elif event > received:
                problem = "price_clock_inversion"
            elif event <= decision_ns:
                problem = "predecision_event_received_late"
            elif received - event > rules["max_gap_ns"]:
                problem = "stale_future_price"
            elif received - previous > rules["max_gap_ns"]:
                problem = "price_gap"
            elif row["event_id"] in seen:
                problem = "duplicate_price_event"
            if problem:
                result["reason"] = problem
                return result
            try:
                price = _decimal(row["mid_px"])
                if price <= 0:
                    raise ValueError("Nonpositive mid")
            except ValueError:
                result["reason"] = "invalid_future_mid"
                return result
            groups[received].append(price); seen.add(row["event_id"]); previous = received
        if end - previous > rules["max_gap_ns"]:
            result["reason"] = "stale_terminal_price"
            return result
        up = next((t for t, values in groups.items() if max(values) >= upper), None)
        down = next((t for t, values in groups.items() if min(values) <= lower), None)
        result.update(first_upper_ns=up, first_lower_ns=down)
        if up is not None and up == down:
            result.update(label_status="ambiguous", reason="simultaneous_first_hits")
            return result
        terminal_values = set(groups[previous])
        if len(terminal_values) != 1:
            result["reason"] = "conflicting_terminal_prices"
            return result
        terminal = next(iter(terminal_values))
        future_return = float((terminal / reference - 1) * 10000)
        if not math.isfinite(future_return):
            result["reason"] = "return_overflow"
            return result
        label = 0 if up is None and down is None else (1 if down is None or (up is not None and up < down) else -1)
        result.update(label=label, label_status={1: "upper_first", -1: "lower_first", 0: "neither"}[label],
                      reason="available", upper_hit=up is not None, lower_hit=down is not None,
                      terminal_received_ns=previous, terminal_age_ns=end - previous,
                      terminal_mid=float(terminal), future_return_bps=future_return)
        return result


def _bin(value):
    if value is None or not math.isfinite(value) or not -1 <= value <= 1:
        return None
    for i, (a, b) in enumerate(zip(BIN_EDGES, BIN_EDGES[1:])):
        if a <= value < b or (i == 3 and value == b):
            return i
    return None


def _bins(rows, field):
    result = []
    for i, (a, b) in enumerate(zip(BIN_EDGES, BIN_EDGES[1:])):
        candidate = [r for r in rows if _bin(r[field]) == i]
        valid = [r for r in candidate if r["label"] is not None]
        counts = {key: sum(r["label_status"] == key for r in valid) for key in LABELS}
        result.append({"bin": f"[{a},{b}{']' if i == 3 else ')'}", "candidate_anchors": len(candidate),
                       "valid_outcomes": len(valid), "excluded_outcomes": len(candidate) - len(valid),
                       "label_counts": counts, "frequencies": {k: v / len(valid) if valid else None for k, v in counts.items()},
                       "mean_return_bps": statistics.mean(r["future_return_bps"] for r in valid) if valid else None})
    return result


def _outcome_summary(rows):
    valid = [r for r in rows if r["label"] is not None]
    censored = sum(r["label_status"] == "censored" for r in rows)
    ambiguous = sum(r["label_status"] == "ambiguous" for r in rows)
    counts = {key: sum(r["label_status"] == key for r in valid) for key in LABELS}
    components, right = 0, -1
    for row in sorted(valid, key=lambda r: r["decision_ns"]):
        if row["decision_ns"] >= right:
            components += 1
        right = max(right, row["label_end_ns"])
    return {"candidate_anchors": len(rows), "valid_outcomes": len(valid), "censored": censored,
            "ambiguous": ambiguous, "excluded_fraction": (len(rows) - len(valid)) / len(rows) if rows else None,
            "exclusion_reasons": dict(Counter(r["reason"] for r in rows if r["label"] is None)),
            "label_counts": counts, "mean_return_bps": statistics.mean(r["future_return_bps"] for r in valid) if valid else None,
            "median_return_bps": statistics.median(r["future_return_bps"] for r in valid) if valid else None,
            "overlapping_forward_window_components": components,
            "lgi_bins": _bins(rows, "lgi")}


def study_events(features, contexts, books, policy, rules):
    """Descriptive anchor outcomes and an optional, identity-aligned OBI comparison."""
    validate_policy(policy); validate_rules(rules)
    if len({r["decision_ns"] for r in features}) != len(features):
        raise ValueError("Duplicate decision anchor")
    for row in contexts + books:
        if (row["source_kind"], row["source"], row["coin"]) != (policy["source_kind"], policy["source"], policy["asset"]):
            raise ValueError("Mixed event study provenance")
    prices = sorted(contexts, key=lambda r: (r["ingested_at"], r["event_id"]))
    times, book_timeline, outcomes = [r["ingested_at"] for r in prices], _timeline(books), []
    for feature in sorted(features, key=lambda r: r["decision_ns"]):
        decision = feature["decision_ns"]
        problem = None
        if feature["status"] != "available":
            problem = "phase2_ineligible"
        elif any(type(feature.get(k)) is not int or feature[k] > decision
                 for k in ("feature_max_received_ns", "feature_max_event_ns")):
            problem = "feature_not_available_at_decision"
        elif _bin(feature.get("lgi")) is None:
            problem = "invalid_lgi"
        book, obi_problem = _market_at(book_timeline, decision, policy)
        obi = None
        if book is not None:
            if (book["timestamp"], book["ingested_at"]) != (feature.get("bbo_event_ns"), feature.get("bbo_received_ns")):
                obi_problem = "phase2_bbo_identity_mismatch"
            else:
                try:
                    bid_ask_spread(book["bid_px"], book["ask_px"])
                    obi = order_book_imbalance(book["bid_sz"], book["ask_sz"])
                    if obi is None:
                        obi_problem = "zero_book_size"
                except ValueError:
                    obi_problem = "invalid_book"
        if obi_problem:
            obi = None
        for horizon in rules["horizons_seconds"]:
            end = decision + horizon * 10**9
            if problem:
                outcome = {"label": None, "label_status": "censored", "reason": problem,
                           "label_end_ns": end, "future_return_bps": None}
            else:
                window = prices[bisect_right(times, decision):bisect_right(times, end)]
                outcome = observed_outcome(window, decision_ns=decision, horizon_seconds=horizon,
                    reference_price=feature["reference_mid"], policy=policy, rules=rules)
            outcomes.append({"decision_ns": decision, "horizon_seconds": horizon, "lgi": feature.get("lgi"),
                "obi": obi, "obi_exclusion_reason": obi_problem,
                "reference_mid": feature.get("reference_mid"),
                "nonoverlap_anchor": (decision - policy["start_ns"]) % (horizon * 10**9) == 0, **outcome})
    summaries = {}
    for horizon in rules["horizons_seconds"]:
        local = [r for r in outcomes if r["horizon_seconds"] == horizon]
        summary = _outcome_summary(local)
        common = [r for r in local if r["label"] is not None and r["obi"] is not None]
        summary.update(obi_available_anchors=sum(r["obi"] is not None for r in local),
            obi_exclusion_reasons=dict(Counter(r["obi_exclusion_reason"] for r in local if r["obi"] is None)),
            common_valid_outcomes=len(common), lgi_bins_common=_bins(common, "lgi"),
            obi_bins_common=_bins(common, "obi"),
            nonoverlap_sensitivity=_outcome_summary([r for r in local if r["nonoverlap_anchor"]]))
        summaries[str(horizon)] = summary
    return {"source_kind": policy["source_kind"], "rules": dict(rules), "phase2_policy": dict(policy),
            "horizons": summaries, "outcomes": outcomes,
            "exploratory_only": True, "iid_inference_performed": False, "prediction_validated": False}
