"""Point-in-time portfolio marks and a simple fully collateralized entry check."""

from decimal import Decimal

from .execution_contracts import number

ZERO = Decimal(0)


def quote_problem(book, now, session_id, max_age_ns):
    if book is None:
        return "missing_quote"
    if not book["valid"]:
        return "invalid_quote"
    if book["session_id"] != session_id:
        return "session_changed"
    if book["available_ns"] > now:
        return "quote_not_yet_available"
    if now - book["timestamp_ns"] > max_age_ns:
        return "stale_quote"
    return None


def account_value(cash, positions, books, now, max_age_ns, limits=None):
    """Keep known cash/reservations even when aggregate equity is unavailable."""
    reserved, unrealized, gross = ZERO, ZERO, ZERO
    marks, unavailable, symbol_gross = {}, [], {}
    for symbol in sorted(positions):
        position = positions[symbol]
        reserved += position["entry_notional"]
        book = books.get(symbol)
        problem = quote_problem(book, now, position["session_id"], max_age_ns)
        mark = dict(quote_id=book["quote_id"] if book else None, reason=problem,
                    quote_event_ns=book["timestamp_ns"] if book else None,
                    quote_available_ns=book["available_ns"] if book else None, mid=None)
        marks[symbol] = mark
        if problem:
            unavailable.append(symbol)
            symbol_gross[symbol] = None
            continue
        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
        mark["mid"] = mid
        notional = position["quantity"] * mid
        symbol_gross[symbol] = notional
        sign = 1 if position["direction"] == "long" else -1
        unrealized += sign * (notional - position["entry_notional"])
        gross += notional
    equity = cash + unrealized if not unavailable else None
    required = max(reserved, gross) if not unavailable else None
    result = dict(timestamp_ns=now, cash=cash, reserved_margin=reserved,
                unrealized_pnl=unrealized if not unavailable else None,
                equity=equity, gross_notional=gross if not unavailable else None,
                free_collateral=equity - reserved if equity is not None else None,
                required_collateral=required,
                collateral_headroom=equity - required if equity is not None else None,
                unavailable_symbols=unavailable, marks=marks)
    if limits is not None:
        result["symbol_gross"] = symbol_gross
        result["limit_checks"] = exposure_checks(result["gross_notional"], symbol_gross, limits)
    return result


def _limit_check(name, value, cap, symbol=None):
    cap = number(cap, positive=True)
    return dict(name=name, symbol=symbol, value=value, limit=cap,
                headroom=cap - value if value is not None else None,
                passed=value <= cap if value is not None else None)


def exposure_checks(gross, symbol_gross, limits):
    """Known violations remain visible even if a different mark is unavailable."""
    checks = [_limit_check("max_symbol_gross", value, limits["max_symbol_gross"][symbol], symbol)
              for symbol, value in sorted(symbol_gross.items())]
    checks.append(_limit_check("max_portfolio_gross", gross, limits["max_portfolio_gross"]))
    return checks


def check_entry(before, *, quantity, notional, mid, direction, fee,
                limits=None, symbol=None, order_notional=None):
    """No netting: both total entry reservations and gross marks must fit equity."""
    if before["equity"] is None:
        return None, "portfolio_mark_unavailable"
    sign = 1 if direction == "long" else -1
    equity = before["equity"] - fee + sign * (quantity * mid - notional)
    reserved = before["reserved_margin"] + notional
    gross = before["gross_notional"] + quantity * mid
    required = max(reserved, gross)
    projected = dict(cash=before["cash"] - fee, equity=equity,
                     unrealized_pnl=equity - (before["cash"] - fee),
                     reserved_margin=reserved, gross_notional=gross,
                     required_collateral=required, free_collateral=equity - reserved,
                     collateral_headroom=equity - required)
    limit_reason = None
    if limits is not None:
        symbol_gross = dict(before["symbol_gross"])
        symbol_gross[symbol] = quantity * mid
        checks = [_limit_check("max_order_notional", order_notional, limits["max_order_notional"], symbol)]
        checks += exposure_checks(gross, symbol_gross, limits)
        projected["limit_checks"] = checks
        failures = [check for check in checks if check["passed"] is False]
        if failures:
            limit_reason = failures[0]["name"] + "_exceeded"
    return projected, "insufficient_collateral" if equity < required else limit_reason
