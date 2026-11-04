"""Bounded synthetic replay inputs sharing one collateral currency."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

KIND = "SYNTHETIC_TEST_ONLY"
QUANTUM = Decimal("0.00000001")


def number(value, *, positive=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("Amounts must be bounded decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid decimal amount") from error
    if (not result.is_finite() or len(result.as_tuple().digits) > 24
            or abs(result.as_tuple().exponent) > 12
            or (result <= 0 if positive else result < 0)):
        raise ValueError("Amount outside finite nonnegative decimal bounds")
    return result


def money(value):
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def fields(row, expected):
    if not isinstance(row, dict) or set(row) != set(expected.split()):
        raise ValueError("Unexpected or missing input fields: " + expected)


def stamp(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Time must be nonnegative integer nanoseconds below 2**63")


def identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError("A bounded identity string is required")


def validate(bundle):
    if not isinstance(bundle, dict):
        raise ValueError("A replay bundle dictionary is required")
    single = "instrument" in bundle
    fields(bundle, "source_kind instrument policy quotes orders" if single else
           "source_kind instruments policy quotes orders")
    if bundle["source_kind"] != KIND:
        raise ValueError("Only SYNTHETIC_TEST_ONLY inputs are supported")
    items = [bundle["instrument"]] if single else bundle["instruments"]
    if type(items) is not list or not 1 <= len(items) <= 100:
        raise ValueError("Between 1 and 100 instruments required")
    instruments = {}
    for instrument in items:
        fields(instrument, "symbol price_tick quantity_step min_notional")
        identity(instrument["symbol"])
        if instrument["symbol"] in instruments:
            raise ValueError("Duplicate instrument symbol")
        instruments[instrument["symbol"]] = {
            key: number(instrument[key], positive=True)
            for key in ("price_tick", "quantity_step", "min_notional")
        }
    policy = bundle["policy"]
    if not isinstance(policy, dict):
        raise ValueError("Execution policy must be a dictionary")
    fields(policy, "start_ns end_ns initial_cash fee_bps latency_ns max_quote_age_ns"
           + (" risk_limits" if "risk_limits" in policy else ""))
    if "risk_limits" in policy:
        limits = policy["risk_limits"]
        fields(limits, "max_order_notional max_symbol_gross max_portfolio_gross")
        number(limits["max_order_notional"], positive=True)
        number(limits["max_portfolio_gross"], positive=True)
        if not isinstance(limits["max_symbol_gross"], dict) or set(limits["max_symbol_gross"]) != set(instruments):
            raise ValueError("Explicit gross limits for every registered symbol required")
        for cap in limits["max_symbol_gross"].values():
            number(cap, positive=True)
    for name in ("start_ns", "end_ns", "latency_ns", "max_quote_age_ns"):
        stamp(policy[name])
    if policy["end_ns"] <= policy["start_ns"] or not policy["max_quote_age_ns"]:
        raise ValueError("Nonempty replay interval and positive quote age required")
    cash = number(policy["initial_cash"], positive=True)
    if cash != money(cash) or number(policy["fee_bps"]) > 1000:
        raise ValueError("Cash quantum is 1e-8; fee assumption must be at most 1000 bps")
    for name in ("quotes", "orders"):
        if type(bundle[name]) is not list or len(bundle[name]) > 10000:
            raise ValueError("At most 10000 quotes/orders per replay")
    quote_times = set()
    for name in ("quotes", "orders"):
        seen = set()
        for row in bundle[name]:
            if name == "quotes":
                fields(row, "quote_id timestamp_ns available_ns session_id valid bids asks"
                       + ("" if single else " symbol"))
                event_id = row["quote_id"]
                time_fields = ("timestamp_ns", "available_ns")
            else:
                fields(row, "order_id decision_ns signal_available_ns expires_ns "
                            "session_id action direction quantity" + ("" if single else " symbol"))
                event_id = row["order_id"]
                time_fields = ("decision_ns", "signal_available_ns", "expires_ns")
            identity(event_id)
            identity(row["session_id"])
            symbol = items[0]["symbol"] if single else row["symbol"]
            identity(symbol)
            if symbol not in instruments:
                raise ValueError("Quote/order symbol is not registered")
            tick = instruments[symbol]["price_tick"]
            step = instruments[symbol]["quantity_step"]
            if event_id in seen:
                raise ValueError("Duplicate " + name + " identity")
            seen.add(event_id)
            for key in time_fields:
                stamp(row[key])
            if name == "orders":
                if (row["action"] not in ("open", "close")
                        or row["direction"] not in ("long", "short")
                        or not policy["start_ns"] <= row["decision_ns"] <= policy["end_ns"]
                        or row["signal_available_ns"] > row["decision_ns"]
                        or row["expires_ns"] < row["decision_ns"]):
                    raise ValueError("Invalid action, direction or order time footprint")
                if number(row["quantity"], positive=True) % step:
                    raise ValueError("Order quantity must match quantity_step")
                continue
            if (type(row["valid"]) is not bool
                    or not policy["start_ns"] <= row["timestamp_ns"]
                    <= row["available_ns"] <= policy["end_ns"]):
                raise ValueError("Invalid quote validity or time footprint")
            quote_time = (symbol, row["timestamp_ns"])
            if quote_time in quote_times:
                raise ValueError("Conflicting quote event time")
            quote_times.add(quote_time)
            for side in ("bids", "asks"):
                levels = row[side]
                if (type(levels) is not list or len(levels) > 50
                        or (row["valid"] and not levels)):
                    raise ValueError("Valid quotes require 1 to 50 levels on each side")
                prices = []
                for level in levels:
                    if not isinstance(level, (list, tuple)) or len(level) != 2:
                        raise ValueError("Each level is a price/quantity pair")
                    price, quantity = [number(v, positive=True) for v in level]
                    if price % tick or quantity % step:
                        raise ValueError("Quote tick/lot mismatch")
                    prices.append(price)
                if prices != sorted(set(prices), reverse=side == "bids"):
                    raise ValueError("Book levels must have unique ordered prices")
            if (row["valid"] and number(row["bids"][0][0])
                    >= number(row["asks"][0][0])):
                raise ValueError("Valid replay books require bid < ask")
    normalized = deepcopy(bundle)
    normalized["single_instrument"] = single
    if single:
        normalized["instruments"] = [normalized.pop("instrument")]
        for row in normalized["quotes"] + normalized["orders"]:
            row["symbol"] = items[0]["symbol"]
    return normalized, instruments
