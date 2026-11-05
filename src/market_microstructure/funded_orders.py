"""Admission and reservations around the synthetic order lifecycle reducer."""

from copy import deepcopy
from decimal import localcontext

from .execution_contracts import KIND, fields, number
from .order_lifecycle import OrderManager
from . import order_protocol as protocol
from . import order_reservations as funding


class FundedOrderManager:
    """Single-owner cash-funded buys and inventory-backed sells."""

    def __init__(self, instruments, policy, venue_id="synthetic-venue"):
        self._manager = OrderManager(instruments, venue_id)
        self._policy = funding.policy(policy, self._manager.instruments)
        self._log = []
        self._last_decision_ns = None

    def _capacity(self):
        self._manager._check_owner()
        if len(self._log) >= protocol.MAX_EVENTS:
            raise ValueError("Funded lifecycle admission bound reached")

    def _quotes(self, marks, now_ns):
        self._manager._check_owner()
        quotes = funding.marks(marks, self._manager.instruments, now_ns)
        if self._last_decision_ns is not None and now_ns < self._last_decision_ns:
            raise ValueError("Funded account decision clock cannot move backwards")
        return quotes

    def submit(self, order, *, now_ns, marks):
        """Return an outgoing order only after atomically reserving its resources."""
        self._capacity()
        quotes = self._quotes(marks, now_ns)
        trial = deepcopy(self._manager)
        outgoing = trial.submit(order)
        before = funding.assess(self._manager.snapshot(), self._policy, quotes, now_ns)
        projected = funding.assess(trial.snapshot(), self._policy, quotes, now_ns)
        balances = projected["account"]
        reason = None
        if balances["available_cash"] < 0:
            reason = "insufficient_cash"
        elif any(qty < 0 for qty in balances["available_inventory"].values()):
            reason = "insufficient_inventory"
        # Selling only unlocked existing inventory reduces exposure; missing marks
        # or an existing gross-limit breach must not block that reduction.
        if outgoing["side"] == "buy":
            with localcontext() as context:
                context.prec = 128
                notional = number(outgoing["quantity"]) * number(outgoing["limit_price"])
                cap = number(self._policy["risk_limits"]["max_order_notional"])
                check = dict(name="max_order_notional", symbol=outgoing["symbol"],
                             value=notional, limit=cap, headroom=cap - notional, passed=notional <= cap)
            projected["limit_checks"].insert(0, check)
            if reason is None:
                if projected["unavailable_symbols"]:
                    reason = "portfolio_mark_unavailable"
                else:
                    failures = [check for check in projected["limit_checks"] if check["passed"] is False]
                    if failures:
                        reason = failures[0]["name"] + "_exceeded"
        if reason is None:
            self._manager = trial
        self._log.append(dict(action="submit", order=deepcopy(outgoing), now_ns=now_ns, marks=quotes,
                              accepted=reason is None, reason=reason))
        self._last_decision_ns = now_ns
        return funding.serialize(dict(accepted=reason is None, reason=reason,
                                      order=outgoing if reason is None else None,
                                      before=before, projected=projected))

    def _action(self, action, **arguments):
        self._manager._check_owner()
        trial = deepcopy(self._manager)
        outcome = getattr(trial, action)(**arguments)
        balances = funding.account(trial.snapshot(), self._policy)
        if balances["available_cash"] < 0 or any(q < 0 for q in balances["available_inventory"].values()):
            raise ValueError("Report violates funded account invariants")
        self._manager = trial
        self._log.append(dict(action=action, **deepcopy(arguments)))
        return outcome

    def apply(self, report):
        """Apply valid executions regardless of current marks or limit breaches."""
        return self._action("apply", report=report)

    def request_cancel(self, client_order_id):
        return self._action("request_cancel", client_order_id=client_order_id)

    def mark_unknown(self, client_order_id):
        return self._action("mark_unknown", client_order_id=client_order_id)

    def snapshot(self):
        lifecycle = self._manager.snapshot()
        return dict(source_kind=KIND, account_model="cash-funded-no-fees-v1", lifecycle=lifecycle,
                    account=funding.serialize(funding.account(lifecycle, self._policy)))

    def assess(self, *, now_ns, marks):
        quotes = self._quotes(marks, now_ns)
        return funding.serialize(funding.assess(self._manager.snapshot(), self._policy, quotes, now_ns))

    def export_log(self):
        """Self-contained replay input; in-memory, not a durable journal."""
        self._manager._check_owner()
        return deepcopy(dict(source_kind=KIND, schema="funded-orders-log-v1",
                             instruments=self._manager.instruments, policy=self._policy,
                             venue_id=self._manager.venue_id, events=self._log))

    @classmethod
    def replay(cls, bundle):
        fields(bundle, "source_kind schema instruments policy venue_id events")
        if bundle["source_kind"] != KIND or bundle["schema"] != "funded-orders-log-v1":
            raise ValueError("Synthetic funded log required")
        events = bundle["events"]
        if type(events) is not list:
            raise ValueError("Funded log list required")
        manager = cls(bundle["instruments"], bundle["policy"], bundle["venue_id"])
        for entry in events:
            action = entry.get("action") if isinstance(entry, dict) else None
            if action == "submit":
                fields(entry, "action order now_ns marks accepted reason")
                if type(entry["accepted"]) is not bool:
                    raise ValueError("Boolean admission outcome required")
                result = manager.submit(entry["order"], now_ns=entry["now_ns"], marks=entry["marks"])
                if result["accepted"] != entry["accepted"] or result["reason"] != entry["reason"]:
                    raise ValueError("Replayed admission outcome changed")
            elif action == "apply":
                fields(entry, "action report")
                manager.apply(entry["report"])
            elif action in ("request_cancel", "mark_unknown"):
                fields(entry, "action client_order_id")
                getattr(manager, action)(entry["client_order_id"])
            else:
                raise ValueError("Unknown funded log operation")
        return manager
