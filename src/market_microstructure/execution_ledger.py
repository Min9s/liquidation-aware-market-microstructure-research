"""Rebuild synthetic collateral accounting from fills, without matching orders."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext

from .execution_contracts import identity, money, number

ZERO = Decimal(0)


def _amount(value):
    if not isinstance(value, (str, Decimal)) or len(str(value)) > 256:
        raise ValueError("Bounded decimal ledger amount required")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid ledger decimal") from error
    if (not result.is_finite() or len(result.as_tuple().digits) > 256
            or abs(result.as_tuple().exponent) > 256):
        raise ValueError("Finite ledger amount required")
    return result


def rebuild_account(fills, *, initial_cash, fee_bps):
    """Accept raw Decimal or serialized fill facts and check each cash transition.

    Duplicate fill order IDs are errors here. Delivery deduplication belongs to the
    journal, before this reducer. This reconstructs accounting, not market depth.
    """
    with localcontext() as context:
        context.prec = 128
        context.rounding = ROUND_HALF_EVEN
        cash = money(_amount(initial_cash))
        rate = number(fee_bps)
        positions, seen = {}, set()
        gross_total = fee_total = ZERO
        for fill in fills:
            for key in ("order_id", "symbol", "session_id"):
                identity(fill[key])
            if fill["order_id"] in seen:
                raise ValueError("Duplicate fill in ledger")
            seen.add(fill["order_id"])
            symbol = fill["symbol"]
            position = positions.get(symbol)
            q, notional = _amount(fill["quantity"]), _amount(fill["notional"])
            if q <= 0 or notional <= 0 or fill["direction"] not in ("long", "short"):
                raise ValueError("Invalid fill facts in ledger")
            if fill["action"] == "open":
                if position is not None:
                    raise ValueError("Overlapping entry in fill ledger")
                positions[symbol] = dict(quantity=q, entry_notional=notional,
                                         direction=fill["direction"], session_id=fill["session_id"])
                gross = ZERO
            elif fill["action"] == "close":
                if (position is None or q > position["quantity"]
                        or position["direction"] != fill["direction"]
                        or position["session_id"] != fill["session_id"]):
                    raise ValueError("Unmatched close in fill ledger")
                released = (position["entry_notional"] if q == position["quantity"] else
                            position["entry_notional"] * q / position["quantity"])
                gross = money((1 if position["direction"] == "long" else -1) * (notional - released))
                position["quantity"] -= q
                position["entry_notional"] -= released
                if not position["quantity"]:
                    del positions[symbol]
            else:
                raise ValueError("Invalid fill action in ledger")
            fee = money(notional * rate / 10000)
            cash += gross - fee
            if (gross != _amount(fill["gross_realized_pnl"]) or fee != _amount(fill["fee"])
                    or cash != _amount(fill["cash_after"])):
                raise ValueError("Fill cash ledger reconciliation failed")
            gross_total += gross
            fee_total += fee
        return dict(cash=cash, positions=dict(sorted(positions.items())),
                    realized_pnl=gross_total, fees=fee_total,
                    reserved_margin=sum((p["entry_notional"] for p in positions.values()), ZERO))
