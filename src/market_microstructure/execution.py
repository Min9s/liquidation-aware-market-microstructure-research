"""Synthetic linear-contract IOC replay sharing a single collateral account.

No exchange API, strategy signal generation, leverage or persistent order lifecycle.
One position per symbol; each new full quote replaces that symbol's displayed depth.
"""

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .execution_contracts import KIND, money, number, validate
from .execution_ledger import rebuild_account
from .execution_risk import account_value, check_entry, quote_problem

ZERO = Decimal(0)


def _public(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


def _match(levels, requested):
    """Draft first; commit displayed-depth changes only after all checks pass."""
    used, quantity, notional = [], ZERO, ZERO
    for index, (price, available) in enumerate(levels):
        take = min(requested - quantity, available)
        if take:
            used.append((index, take))
            quantity += take
            notional += price * take
        if quantity == requested:
            break
    return quantity, notional, used


def replay_execution(bundle):
    """Replay validated artificial books/orders without mutating caller inputs.

    Equal-time priority: quote, close, open; IDs break remaining ties. Cash is
    collateral: entry notional is reserved, not paid as a spot purchase. Fee is
    rounded per fill to 1e-8 with HALF_EVEN. No funding or impact costs are modeled.
    """
    with localcontext() as context:
        context.prec = 128
        context.rounding = ROUND_HALF_EVEN
        normalized, instruments = validate(bundle)
        result = _replay(normalized, instruments)
        result["reconciliation"] = _reconcile(result)
        return _public(result)


def _replay(bundle, instruments):
    policy = bundle["policy"]
    limits = policy.get("risk_limits")
    initial = number(policy["initial_cash"])
    cash, realized, fees = money(initial), ZERO, ZERO
    positions, books = {}, {}
    orders, fills, ignored = [], [], []
    events = [(q["available_ns"], 0, q["quote_id"], "quote", q) for q in bundle["quotes"]]
    for row in bundle["orders"]:
        events.append((row["decision_ns"] + policy["latency_ns"],
                       1 if row["action"] == "close" else 2, row["order_id"], "order", row))
    events.sort(key=lambda e: e[:3])

    def account(now):
        return account_value(cash, positions, books, now, policy["max_quote_age_ns"], limits)

    for now, _, _, kind, row in events:
        symbol = row["symbol"]
        book = books.get(symbol)
        if kind == "quote":
            if book is not None and row["timestamp_ns"] < book["timestamp_ns"]:
                ignored_row = {"quote_id": row["quote_id"], "reason": "older_event_timestamp"}
                if not bundle["single_instrument"]:
                    ignored_row["symbol"] = symbol
                ignored.append(ignored_row)
                continue
            book = deepcopy(row)
            for side in ("bids", "asks"):
                book[side] = [[number(p), number(q)] for p, q in row[side]]
            books[symbol] = book
            continue
        requested = number(row["quantity"])
        order = dict(order_id=row["order_id"], action=row["action"], direction=row["direction"],
                     decision_ns=row["decision_ns"], arrival_ns=now, requested_quantity=requested,
                     filled_quantity=ZERO, unfilled_quantity=requested, status="rejected",
                     reason=None, quote_id=None, symbol=symbol, risk=None)
        orders.append(order)
        if now > policy["end_ns"]:
            order.update(status="censored", reason="arrival_after_replay_end")
            continue
        before = account(now)
        risk = dict(status="not_evaluated", before=before, candidate=None,
                    projected=None, after=None, reason=None)
        order["risk"] = risk
        if now > row["expires_ns"]:
            order["reason"] = "expired_before_arrival"
            continue
        problem = quote_problem(book, now, row["session_id"], policy["max_quote_age_ns"])
        if problem:
            order["reason"] = "quote_missing_stale_invalid_or_session_changed"
            risk["reason"] = problem
            continue
        order["quote_id"] = book["quote_id"]
        opening = row["action"] == "open"
        position = positions.get(symbol)
        if opening and position is not None:
            order["reason"] = "position_already_open"
            continue
        if not opening and (position is None or position["direction"] != row["direction"]
                            or position["session_id"] != row["session_id"]):
            order["reason"] = "close_position_missing_or_binding_differs"
            continue
        target = requested if opening else min(requested, position["quantity"])
        buy = (row["direction"] == "long") == opening
        levels = book["asks" if buy else "bids"]
        filled, notional, used = _match(levels, target)
        if not filled:
            order["reason"] = "no_remaining_displayed_depth"
            continue
        fee = money(notional * number(policy["fee_bps"]) / 10000)
        vwap = notional / filled
        gross = ZERO
        sign = Decimal(1 if row["direction"] == "long" else -1)
        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
        risk["candidate"] = dict(quantity=filled, notional=notional, fee=fee, mid=mid)
        if opening:
            if notional < instruments[symbol]["min_notional"]:
                order["reason"] = "filled_entry_below_minimum_notional"
                continue
            # Assess requested exposure too: a tiny visible depth must not hide an oversized order.
            order_notional = max(requested * levels[0][0], notional)
            projected, problem = check_entry(before, quantity=filled, notional=notional,
                                             mid=mid, direction=row["direction"], fee=fee,
                                             limits=limits, symbol=symbol, order_notional=order_notional)
            risk.update(status="rejected" if problem else "approved",
                        projected=projected, reason=problem)
            if problem:
                order["reason"] = problem
                continue
            positions[symbol] = dict(direction=row["direction"], quantity=filled,
                                     entry_notional=notional, session_id=row["session_id"])
        else:
            released = (position["entry_notional"] if filled == position["quantity"] else
                        position["entry_notional"] * filled / position["quantity"])
            gross = money(sign * (notional - released))
            position["quantity"] -= filled
            position["entry_notional"] -= released
            if not position["quantity"]:
                del positions[symbol]
            risk["status"] = "reduce_only"
        cash += gross - fee
        realized += gross
        fees += fee
        for index, take in used:
            levels[index][1] -= take
        fills.append(dict(order_id=row["order_id"], symbol=symbol,
                          session_id=row["session_id"], action=row["action"], direction=row["direction"],
                          timestamp_ns=now, quote_id=book["quote_id"],
                          quote_available_ns=book["available_ns"], quantity=filled, notional=notional,
                          vwap=vwap, fee=fee, gross_realized_pnl=gross, cash_after=cash))
        reason = ("ioc_filled" if filled == requested else
                  "close_capped_to_position" if filled == target else "ioc_depth_partial")
        order.update(filled_quantity=filled, unfilled_quantity=requested - filled,
                     status="filled" if filled == requested else "partial", reason=reason)
        risk["after"] = account(now)

    for order in orders:
        risk = order["risk"]
        if risk and risk["status"] == "not_evaluated" and risk["reason"] is None:
            risk["reason"] = order["reason"]
    terminal = account(policy["end_ns"])
    result = dict(schema="synthetic-execution-result-v2", source_kind=KIND,
                instruments=deepcopy(bundle["instruments"]),
                policy=deepcopy(policy), orders=orders, fills=fills, ignored_quotes=ignored,
                initial_cash=money(initial), terminal_cash=cash, realized_pnl=realized, fees=fees,
                terminal_positions=deepcopy(dict(sorted(positions.items()))), terminal_account=terminal,
                terminal_unrealized_pnl=terminal["unrealized_pnl"],
                terminal_equity=terminal["equity"], terminal_flat=not positions, actual_orders_sent=0)
    if bundle["single_instrument"]:
        instrument = bundle["instruments"][0]
        result["instrument"] = deepcopy(instrument)
        result["terminal_position"] = deepcopy(positions.get(instrument["symbol"],
            dict(direction=None, quantity=ZERO, entry_notional=ZERO, session_id=None)))
    return result


def _reconcile(result):
    """Compare fill-derived facts against engine totals and compatibility fields."""
    state = rebuild_account(result["fills"], initial_cash=result["initial_cash"],
                            fee_bps=result["policy"]["fee_bps"])
    if (state["cash"] != result["terminal_cash"]
            or state["realized_pnl"] != result["realized_pnl"]
            or state["fees"] != result["fees"]
            or state["positions"] != result["terminal_positions"]
            or state["cash"] != result["terminal_account"]["cash"]
            or state["reserved_margin"] != result["terminal_account"]["reserved_margin"]):
        raise ValueError("Terminal ledger reconciliation failed")
    if "terminal_position" in result:
        symbol = result["instrument"]["symbol"]
        legacy = state["positions"].get(symbol, dict(direction=None, quantity=ZERO,
                                                     entry_notional=ZERO, session_id=None))
        if legacy != result["terminal_position"]:
            raise ValueError("Single-instrument ledger reconciliation failed")
    return dict(cash=True, position=True, realized_pnl=True, fees=True)
