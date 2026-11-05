"""Fully funded synthetic cash/inventory; no borrowing, derivatives or fees."""

from decimal import Decimal, localcontext
from copy import deepcopy

from .execution_contracts import KIND, fields, identity, number, stamp
from .execution_risk import exposure_checks, quote_problem
from .order_protocol import TERMINAL


def policy(value, instruments):
    fields(value, "source_kind initial_cash session_id max_quote_age_ns risk_limits")
    if value["source_kind"] != KIND:
        raise ValueError("Synthetic funded policy required")
    number(value["initial_cash"], positive=True)
    identity(value["session_id"])
    stamp(value["max_quote_age_ns"])
    if value["max_quote_age_ns"] == 0:
        raise ValueError("Positive quote age required")
    limits = value["risk_limits"]
    fields(limits, "max_order_notional max_symbol_gross max_portfolio_gross")
    number(limits["max_order_notional"], positive=True)
    number(limits["max_portfolio_gross"], positive=True)
    if not isinstance(limits["max_symbol_gross"], dict) or set(limits["max_symbol_gross"]) != set(instruments):
        raise ValueError("Explicit symbol caps for all registered instruments required")
    for cap in limits["max_symbol_gross"].values():
        number(cap, positive=True)
    return deepcopy(value)


def marks(values, instruments, now):
    stamp(now)
    if not isinstance(values, dict) or not set(values) <= set(instruments):
        raise ValueError("Marks must be a mapping of registered symbols")
    for mark in values.values():
        fields(mark, "quote_id timestamp_ns available_ns session_id valid price")
        identity(mark["quote_id"])
        identity(mark["session_id"])
        stamp(mark["timestamp_ns"])
        stamp(mark["available_ns"])
        if type(mark["valid"]) is not bool or mark["timestamp_ns"] > mark["available_ns"]:
            raise ValueError("Invalid mark validity or receipt timeline")
        number(mark["price"], positive=True)
    return deepcopy(values)


def serialize(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    return value


def account(snapshot, config):
    """Unconfirmed new/cancel requests keep the full unfilled reservation."""
    with localcontext() as context:
        context.prec = 128
        inventory = {symbol: Decimal(qty) for symbol, qty in snapshot["net_filled_quantity"].items()}
        locked_units = {symbol: Decimal(0) for symbol in inventory}
        locked_cash, reservations = Decimal(0), {}
        for client_id, record in snapshot["orders"].items():
            if record["status"] in TERMINAL:
                continue
            # The reducer validated inputs; exact subtraction can produce more
            # digits than either input. Do not reapply external input bounds.
            spec, remaining = record["order"], Decimal(record["remaining_quantity"])
            cash = remaining * number(spec["limit_price"]) if spec["side"] == "buy" else Decimal(0)
            units = remaining if spec["side"] == "sell" else Decimal(0)
            locked_cash += cash
            locked_units[spec["symbol"]] += units
            reservations[client_id] = dict(symbol=spec["symbol"], cash=cash, inventory=units)
        cash = number(config["initial_cash"]) + Decimal(snapshot["cash_flow"])
        return dict(cash=cash, reserved_cash=locked_cash, available_cash=cash - locked_cash,
                    inventory=inventory, reserved_inventory=locked_units,
                    available_inventory={s: q - locked_units[s] for s, q in inventory.items()},
                    reservations=reservations)


def assess(snapshot, config, quotes, now):
    """No pending-sell netting; pending buys count at max(limit, available mark)."""
    with localcontext() as context:
        context.prec = 128
        balances = account(snapshot, config)
        pending = {symbol: [] for symbol in balances["inventory"]}
        for record in snapshot["orders"].values():
            if record["status"] not in TERMINAL and record["order"]["side"] == "buy":
                pending[record["order"]["symbol"]].append(record)
        gross, holdings, diagnostics = {}, {}, {}
        for symbol, quantity in balances["inventory"].items():
            quote = quotes.get(symbol)
            problem = quote_problem(quote, now, config["session_id"], config["max_quote_age_ns"])
            needed = quantity > 0 or bool(pending[symbol])
            price = number(quote["price"], positive=True) if quote and problem is None else None
            diagnostics[symbol] = dict(quote_id=quote["quote_id"] if quote else None,
                                       timestamp_ns=quote["timestamp_ns"] if quote else None,
                                       available_ns=quote["available_ns"] if quote else None,
                                       price=price, reason=problem, required=needed)
            if quantity == 0:
                holdings[symbol] = Decimal(0)
            else:
                holdings[symbol] = quantity * price if price is not None else None
            if needed and price is None:
                gross[symbol] = None
            else:
                gross[symbol] = holdings[symbol] + sum(
                    (Decimal(r["remaining_quantity"]) * max(price, number(r["order"]["limit_price"]))
                     for r in pending[symbol]), Decimal(0))
        total = sum(gross.values(), Decimal(0)) if all(v is not None for v in gross.values()) else None
        held = sum(holdings.values(), Decimal(0)) if all(v is not None for v in holdings.values()) else None
        return dict(account=balances, timestamp_ns=now, equity=balances["cash"] + held if held is not None else None,
                    committed_gross=total, symbol_committed_gross=gross,
                    limit_checks=exposure_checks(total, gross, config["risk_limits"]), marks=diagnostics,
                    unavailable_symbols=[s for s, v in sorted(gross.items()) if v is None])
