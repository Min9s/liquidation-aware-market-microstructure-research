"""In-memory synthetic order lifecycle and idempotent cumulative-report reducer."""

from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
from threading import current_thread
from weakref import ref

from .execution_contracts import KIND, number
from . import order_protocol as protocol


class OrderManager:
    """Single-owner state; queue foreign-thread callbacks to the creating thread."""

    def __init__(self, instruments, venue_id="synthetic-venue"):
        from .execution_contracts import identity
        identity(venue_id)
        self.instruments = protocol.instruments(instruments)
        self.venue_id = venue_id
        self._orders, self._executions, self._reports = {}, {}, {}
        self._revisions = {}
        self._log = []
        # Weak references retain identity across deepcopy without copying Thread locks.
        self._owner = ref(current_thread())

    def _check_owner(self):
        if self._owner() is not current_thread():
            raise RuntimeError("Order manager must be accessed from its owner thread")

    def _capacity(self):
        self._check_owner()
        if len(self._log) >= protocol.MAX_EVENTS:
            raise ValueError("Synthetic lifecycle admission bound reached")

    def submit(self, order):
        self._capacity()
        spec = protocol.order(order, self.instruments)
        client_id = spec["client_order_id"]
        if client_id in self._orders:
            raise ValueError("Client order ID already registered; reconcile before another action")
        if len(self._orders) >= protocol.MAX_ORDERS:
            raise ValueError("Synthetic lifecycle order bound reached")
        self._orders[client_id] = dict(order=spec, status="pending_new", cancel_status="none", state=None)
        self._log.append(dict(action="submit", order=deepcopy(spec)))
        return deepcopy(spec)

    def mark_unknown(self, client_order_id):
        self._check_owner()
        prior = self._orders[client_order_id]
        candidate = deepcopy(prior)
        if candidate["cancel_status"] == "pending":
            candidate["cancel_status"] = "unknown"
        elif candidate["status"] == "pending_new":
            candidate["status"] = "unknown"
        else:
            raise ValueError("No unanswered request to mark unknown")
        self._orders[client_order_id] = candidate
        self._log.append(dict(action="mark_unknown", client_order_id=client_order_id))

    def request_cancel(self, client_order_id):
        self._check_owner()
        prior = self._orders[client_order_id]
        if prior["status"] not in {"open", "partially_filled"} or prior["cancel_status"] in {"pending", "unknown"}:
            raise ValueError("Cancel requires a known live order with no unresolved cancel request")
        candidate = deepcopy(prior)
        candidate["cancel_status"] = "pending"
        self._orders[client_order_id] = candidate
        self._log.append(dict(action="request_cancel", client_order_id=client_order_id))

    def apply(self, report):
        """Validate a complete candidate before changing order or execution facts."""
        self._check_owner()
        report = protocol.report(report, self.instruments, self.venue_id)
        event_id = report["event_id"]
        encoded = protocol.canonical(report)
        if event_id in self._reports:
            if self._reports[event_id] != encoded:
                raise ValueError("Conflicting report event ID")
            self._log.append(dict(action="apply", report=report))
            return "duplicate"
        state = report["state"]
        spec = state["order"]
        client_id = spec["client_order_id"]
        if client_id not in self._orders:
            raise ValueError("Report for unregistered local order")
        prior = self._orders[client_id]
        if protocol.canonical(spec) != protocol.canonical(prior["order"]):
            raise ValueError("Order binding changed")
        old = prior["state"]
        if any(value["state"] and value["state"]["exchange_order_id"] == state["exchange_order_id"]
               for key, value in self._orders.items() if key != client_id):
            raise ValueError("Exchange order ID reused across client orders")
        if old is not None and state["exchange_order_id"] != old["exchange_order_id"]:
            raise ValueError("Exchange order binding changed")
        revision_key = (client_id, state["revision"])
        fingerprint = sha256(protocol.canonical(state).encode()).digest()
        if revision_key in self._revisions and self._revisions[revision_key] != fingerprint:
            raise ValueError("Conflicting state at an observed revision")
        executions = dict(self._executions)
        for fill in state["fills"]:
            fact = dict(client_order_id=client_id, symbol=spec["symbol"], side=spec["side"], **fill)
            execution_id = fill["execution_id"]
            if execution_id in executions and protocol.canonical(executions[execution_id]) != protocol.canonical(fact):
                raise ValueError("Conflicting execution ID")
            executions[execution_id] = fact
        stale = old is not None and state["revision"] < old["revision"]
        if stale:
            if state["status"] in protocol.TERMINAL or old["status"] == "rejected":
                historical, current = deepcopy(state), deepcopy(old)
                historical.pop("revision")
                current.pop("revision")
                if protocol.canonical(historical) != protocol.canonical(current):
                    raise ValueError("Stale report contradicts terminal order facts")
            if set(executions) != set(self._executions):
                raise ValueError("Stale cumulative report contains unseen executions")
            old_facts = {f["execution_id"] for f in old["fills"]}
            if not {f["execution_id"] for f in state["fills"]} <= old_facts:
                raise ValueError("Stale report is not a historical subset")
            candidate = deepcopy(prior)
            outcome = "stale"
        else:
            if old is not None:
                if state["revision"] == old["revision"] and protocol.canonical(state) != protocol.canonical(old):
                    raise ValueError("Conflicting state at same revision")
                old_ids = {f["execution_id"] for f in old["fills"]}
                if not old_ids <= {f["execution_id"] for f in state["fills"]}:
                    raise ValueError("Cumulative report dropped executions")
                if old["status"] in protocol.TERMINAL:
                    old_terminal, new_terminal = deepcopy(old), deepcopy(state)
                    old_terminal.pop("revision")
                    new_terminal.pop("revision")
                    if protocol.canonical(old_terminal) != protocol.canonical(new_terminal):
                        raise ValueError("Terminal order facts changed")
                elif state["status"] == "rejected":
                    raise ValueError("Accepted order cannot become new-order rejection")
            candidate = deepcopy(prior)
            candidate.update(state=state, status=state["status"])
            if prior["cancel_status"] in {"pending", "unknown"}:
                if state["status"] == "canceled":
                    candidate["cancel_status"] = "canceled"
                elif state["status"] == "filled":
                    candidate["cancel_status"] = "too_late"
            outcome = "applied"
        self._orders[client_id] = candidate
        self._executions = executions
        self._reports[event_id] = encoded
        self._revisions[revision_key] = fingerprint
        self._log.append(dict(action="apply", report=report))
        return outcome

    def snapshot(self):
        self._check_owner()
        with localcontext() as context:
            context.prec = 128
            inventory = {symbol: Decimal(0) for symbol in self.instruments}
            cash_flow = Decimal(0)
            for fact in self._executions.values():
                signed = number(fact["quantity"]) * (1 if fact["side"] == "buy" else -1)
                inventory[fact["symbol"]] += signed
                cash_flow -= signed * number(fact["price"])
            orders = {}
            for key, record in sorted(self._orders.items()):
                state = record["state"]
                filled = sum((number(f["quantity"]) for f in state["fills"]), Decimal(0)) if state else Decimal(0)
                remaining = number(record["order"]["quantity"]) - filled
                working = (None if state is None else "0" if record["status"] in protocol.TERMINAL
                           else format(remaining, "f"))
                orders[key] = dict(order=deepcopy(record["order"]), status=record["status"],
                                  cancel_status=record["cancel_status"], revision=state["revision"] if state else None,
                                  exchange_order_id=state["exchange_order_id"] if state else None,
                                  filled_quantity=format(filled, "f"),
                                  remaining_quantity=format(remaining, "f"),
                                  last_confirmed_working_quantity=working,
                                  needs_reconciliation=record["status"] == "unknown" or record["cancel_status"] == "unknown")
            return dict(source_kind=KIND, venue_id=self.venue_id, orders=orders,
                        net_filled_quantity={s: format(q, "f") for s, q in sorted(inventory.items())},
                        cash_flow=format(cash_flow, "f"), executions=len(self._executions))

    def export_log(self):
        self._check_owner()
        return deepcopy(self._log)

    @classmethod
    def replay(cls, instruments, log, venue_id="synthetic-venue"):
        if type(log) is not list:
            raise ValueError("Lifecycle log list required")
        manager = cls(instruments, venue_id)
        for entry in log:
            action = entry.get("action") if isinstance(entry, dict) else None
            if action == "submit" and set(entry) == {"action", "order"}:
                manager.submit(entry["order"])
            elif action in ("mark_unknown", "request_cancel") and set(entry) == {"action", "client_order_id"}:
                getattr(manager, action)(entry["client_order_id"])
            elif action == "apply" and set(entry) == {"action", "report"}:
                manager.apply(entry["report"])
            else:
                raise ValueError("Unknown lifecycle log operation")
        return manager
