"""Passive risk observations over a frozen synthetic execution replay.

Matching and accounting decisions come from replay_execution. Monitoring rebuilds
positions from actual simulated fills and never generates orders or liquidations.
"""

from copy import deepcopy
from decimal import ROUND_HALF_EVEN, localcontext

from .execution import _public, replay_execution
from .execution_contracts import number, validate
from .execution_ledger import rebuild_account
from .execution_risk import account_value


def monitor_execution(bundle):
    """Return execution, observations, risk transitions and bounded risk episodes.

    Equal-time priority: start, quotes by ID, current-quote expiry, closes by ID,
    opens by ID, end. Observe each atomic step, including zero-duration episodes.
    A quote remains fresh through timestamp + max_age; expiry is one ns later.
    """
    with localcontext() as context:
        context.prec = 128
        context.rounding = ROUND_HALF_EVEN
        normalized, _ = validate(bundle)
        execution = replay_execution(bundle)
        return _public(_monitor(normalized, execution))


def _monitor(bundle, execution):
    policy = bundle["policy"]
    max_age = policy["max_quote_age_ns"]
    timeline = [(policy["start_ns"], -1, "", "start", None),
                (policy["end_ns"], 4, "", "end", None)]
    for row in bundle["quotes"]:
        timeline.append((row["available_ns"], 0, row["quote_id"], "quote", row))
        expiry = row["timestamp_ns"] + max_age + 1
        if row["valid"] and row["available_ns"] <= expiry <= policy["end_ns"]:
            timeline.append((expiry, 1, row["quote_id"], "quote_expiry", row))
    for fill in execution["fills"]:
        priority = 2 if fill["action"] == "close" else 3
        timeline.append((fill["timestamp_ns"], priority, fill["order_id"], "fill", fill))
    timeline.sort(key=lambda event: event[:3])
    books, fills = {}, []
    ledger = rebuild_account([], initial_cash=policy["initial_cash"], fee_bps=policy["fee_bps"])
    observations, transitions, episodes = [], [], []
    prior_signature = ("healthy", (), ())
    active = None
    for now, _, identity, kind, row in timeline:
        symbol = row["symbol"] if row else None
        if kind == "quote":
            old = books.get(symbol)
            if old is not None and row["timestamp_ns"] < old["timestamp_ns"]:
                continue
            book = deepcopy(row)
            for side in ("bids", "asks"):
                book[side] = [[number(p), number(q)] for p, q in row[side]]
            books[symbol] = book
        elif kind == "quote_expiry":
            # An old timer must not expire a newer quote or an unheld instrument.
            if (symbol not in ledger["positions"] or books.get(symbol, {}).get("quote_id") != identity):
                continue
        elif kind == "fill":
            fills.append(row)
            ledger = rebuild_account(fills, initial_cash=policy["initial_cash"], fee_bps=policy["fee_bps"])
        account = account_value(ledger["cash"], ledger["positions"], books, now, max_age,
                                policy.get("risk_limits"))
        breaches = tuple((c["name"], c["symbol"]) for c in account.get("limit_checks", [])
                         if c["passed"] is False)
        if account["equity"] is None:
            status = "valuation_unavailable"
        elif account["collateral_headroom"] < 0:
            status = "collateral_breach"
        elif breaches:
            status = "exposure_limit_breach"
        else:
            status = "healthy"
        reasons = tuple((s, account["marks"][s]["reason"]) for s in account["unavailable_symbols"])
        signature = (status, reasons, breaches)
        observation = dict(sequence=len(observations) + 1, timestamp_ns=now,
                           trigger=dict(kind=kind, identity=identity or None, symbol=symbol),
                           status=status, account=account)
        observations.append(observation)
        if signature != prior_signature:
            prior_status = prior_signature[0]
            if prior_status == "healthy":
                change = "started"
                active = dict(episode_id=len(episodes) + 1, start_ns=now, end_ns=None,
                              observed_until_ns=now, observed_duration_ns=0,
                              last_risk_status=status, open_at_end=True)
                episodes.append(active)
            elif status == "healthy":
                change = "resolved"
                active.update(end_ns=now, observed_until_ns=now,
                              observed_duration_ns=now - active["start_ns"], open_at_end=False)
            else:
                change = "changed"
            transitions.append(dict(sequence=len(transitions) + 1,
                                    observation_sequence=observation["sequence"],
                                    episode_id=active["episode_id"], change=change,
                                    from_status=prior_status, to_status=status,
                                    timestamp_ns=now, trigger=observation["trigger"], account=account))
            if status == "healthy":
                active = None
        if active is not None:
            active.update(observed_until_ns=now, observed_duration_ns=now - active["start_ns"],
                          last_risk_status=status)
        prior_signature = signature
    if _public(observations[-1]["account"]) != execution["terminal_account"]:
        raise ValueError("Monitoring terminal account differs from execution")
    return dict(schema="synthetic-risk-monitor-v1", source_kind=bundle["source_kind"],
                execution=execution, observations=observations, risk_events=transitions,
                episodes=episodes, terminal_status=observations[-1]["status"], automatic_actions=False)
