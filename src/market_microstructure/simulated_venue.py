"""Deterministic synthetic venue: explicit fills and controllable response delivery."""

from copy import deepcopy
from decimal import Decimal, localcontext

from .execution_contracts import KIND, identity, number
from . import order_protocol as protocol


class SimulatedVenue:
    """No sockets or matching model. Caller decides when to execute/deliver reports."""

    def __init__(self, instruments, venue_id="synthetic-venue"):
        identity(venue_id)
        self.venue_id = venue_id
        self.instruments = protocol.instruments(instruments)
        self._orders = {}
        self._report_count = self._execution_count = 0

    def _report(self, kind, snapshot):
        if self._report_count >= protocol.MAX_EVENTS:
            raise ValueError("Synthetic venue report bound reached")
        self._report_count += 1
        return dict(source_kind=KIND, event_id=f"report-{self._report_count}",
                    report_type=kind, state=deepcopy(snapshot))

    def submit(self, order, *, rejection_reason=None):
        spec = protocol.order(order, self.instruments)
        client_id = spec["client_order_id"]
        if rejection_reason is not None:
            identity(rejection_reason)
        prior = self._orders.get(client_id)
        if prior is not None:
            if protocol.canonical(prior["order"]) != protocol.canonical(spec):
                raise ValueError("Conflicting client order ID at venue")
            return self._report("snapshot", prior)
        if len(self._orders) >= protocol.MAX_ORDERS or self._report_count >= protocol.MAX_EVENTS:
            raise ValueError("Synthetic venue bound reached")
        snapshot = dict(venue_id=self.venue_id, exchange_order_id=f"venue-order-{len(self._orders)+1}",
                        revision=1, status="rejected" if rejection_reason else "open",
                        order=spec, fills=[], rejection_reason=rejection_reason)
        self._orders[client_id] = snapshot
        return self._report("rejected" if rejection_reason else "accepted", snapshot)

    def execute(self, client_order_id, quantity, price):
        """Explicit artificial fill; never exceed remaining quantity or the limit."""
        prior = self._orders[client_order_id]
        if prior["status"] in protocol.TERMINAL:
            raise ValueError("Cannot execute a terminal order")
        if self._report_count >= protocol.MAX_EVENTS or len(prior["fills"]) >= protocol.MAX_FILLS:
            raise ValueError("Synthetic venue bound reached")
        candidate = deepcopy(prior)
        fill = dict(execution_id=f"execution-{self._execution_count+1}", quantity=quantity, price=price)
        candidate["fills"].append(fill)
        with localcontext() as context:
            context.prec = 128
            total = sum((number(f["quantity"], positive=True) for f in candidate["fills"]), Decimal(0))
            candidate["status"] = "filled" if total == number(prior["order"]["quantity"]) else "partially_filled"
        candidate["revision"] += 1
        protocol.state(candidate, self.instruments, self.venue_id)
        self._execution_count += 1
        self._orders[client_order_id] = candidate
        return self._report("trade", candidate)

    def cancel(self, client_order_id):
        prior = self._orders[client_order_id]
        if prior["status"] in protocol.TERMINAL:
            return self._report("cancel_rejected", prior)
        if self._report_count >= protocol.MAX_EVENTS:
            raise ValueError("Synthetic venue bound reached")
        candidate = deepcopy(prior)
        candidate.update(status="canceled", revision=prior["revision"] + 1)
        self._orders[client_order_id] = candidate
        return self._report("canceled", candidate)

    def query(self, client_order_id):
        """None is not evidence that a delayed submission can safely be resent."""
        snapshot = self._orders.get(client_order_id)
        return self._report("snapshot", snapshot) if snapshot else None
