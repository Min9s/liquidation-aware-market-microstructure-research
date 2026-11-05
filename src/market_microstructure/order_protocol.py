"""A bounded cumulative-state protocol for one synthetic venue, not exchange FIX."""

from copy import deepcopy
from decimal import Decimal, localcontext
import json

from .execution_contracts import KIND, fields, identity, number

TERMINAL = {"filled", "canceled", "rejected"}
STATUSES = {"open", "partially_filled", *TERMINAL}
REPORT_TYPES = {"accepted", "trade", "canceled", "rejected", "cancel_rejected", "snapshot"}
MAX_ORDERS = 1000
MAX_FILLS = 1000
# Stop new submissions at this audit-history budget; accepted orders must drain.
MAX_EVENTS = 10000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def instruments(values):
    if not isinstance(values, dict) or not 1 <= len(values) <= 100:
        raise ValueError("One to 100 registered synthetic instruments required")
    for symbol, item in values.items():
        identity(symbol)
        fields(item, "price_tick quantity_step")
        for key in item:
            number(item[key], positive=True)
    return deepcopy(values)


def order(value, registry):
    fields(value, "source_kind client_order_id symbol side quantity limit_price")
    identity(value["symbol"])
    if value["source_kind"] != KIND or value["symbol"] not in registry or value["side"] not in ("buy", "sell"):
        raise ValueError("Registered synthetic limit order required")
    identity(value["client_order_id"])
    with localcontext() as context:
        context.prec = 128
        item = registry[value["symbol"]]
        if (number(value["quantity"], positive=True) % number(item["quantity_step"], positive=True)
                or number(value["limit_price"], positive=True) % number(item["price_tick"], positive=True)):
            raise ValueError("Order tick/lot mismatch")
    return deepcopy(value)


def state(value, registry, venue_id):
    fields(value, "venue_id exchange_order_id revision status order fills rejection_reason")
    identity(value["status"])
    if value["venue_id"] != venue_id or value["status"] not in STATUSES:
        raise ValueError("Unexpected venue or order status")
    identity(value["exchange_order_id"])
    if type(value["revision"]) is not int or not 0 < value["revision"] < 2**63:
        raise ValueError("Positive integer venue revision below 2**63 required")
    spec = order(value["order"], registry)
    fills = value["fills"]
    if type(fills) is not list or len(fills) > MAX_FILLS:
        raise ValueError("Bounded cumulative fills required")
    seen = set()
    with localcontext() as context:
        context.prec = 128
        total = Decimal(0)
        item = registry[spec["symbol"]]
        for fill in fills:
            fields(fill, "execution_id quantity price")
            identity(fill["execution_id"])
            if fill["execution_id"] in seen:
                raise ValueError("Duplicate execution in cumulative state")
            seen.add(fill["execution_id"])
            quantity, price = number(fill["quantity"], positive=True), number(fill["price"], positive=True)
            if quantity % number(item["quantity_step"]) or price % number(item["price_tick"]):
                raise ValueError("Fill tick/lot mismatch")
            limit = number(spec["limit_price"])
            if (spec["side"] == "buy" and price > limit) or (spec["side"] == "sell" and price < limit):
                raise ValueError("Fill violates limit price")
            total += quantity
        requested = number(spec["quantity"])
        if total > requested:
            raise ValueError("Cumulative overfill")
        status = value["status"]
        if (status == "open" and total != 0
                or status == "partially_filled" and not 0 < total < requested
                or status == "filled" and total != requested
                or status == "canceled" and total >= requested
                or status == "rejected" and total != 0):
            raise ValueError("Order status disagrees with cumulative fills")
    if value["status"] == "rejected":
        identity(value["rejection_reason"])
    elif value["rejection_reason"] is not None:
        raise ValueError("Rejection reason only belongs to rejected orders")
    return deepcopy(value)


def report(value, registry, venue_id):
    fields(value, "source_kind event_id report_type state")
    identity(value["report_type"])
    if value["source_kind"] != KIND or value["report_type"] not in REPORT_TYPES:
        raise ValueError("Synthetic cumulative report required")
    identity(value["event_id"])
    snapshot = state(value["state"], registry, venue_id)
    allowed = {"accepted": {"open"}, "trade": {"partially_filled", "filled"},
               "canceled": {"canceled"}, "rejected": {"rejected"},
               "cancel_rejected": TERMINAL, "snapshot": STATUSES}
    if snapshot["status"] not in allowed[value["report_type"]]:
        raise ValueError("Report type disagrees with state")
    return deepcopy(value)
