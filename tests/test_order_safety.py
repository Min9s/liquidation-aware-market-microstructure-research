"""Regressions for ownership and admission-budget exhaustion with live orders."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_orders import FundedOrderManager
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.order_lifecycle import OrderManager
from market_microstructure.simulated_venue import SimulatedVenue


ITEMS = {"A": dict(price_tick="1", quantity_step="1")}
POLICY = dict(source_kind=KIND, initial_cash="1000", session_id="s", max_quote_age_ns=10,
              risk_limits=dict(max_order_notional="1000", max_symbol_gross={"A": "2000"},
                               max_portfolio_gross="2000"))
MARKS = {"A": dict(quote_id="q", timestamp_ns=0, available_ns=0, session_id="s", valid=True, price="100")}
ORDER = dict(source_kind=KIND, client_order_id="buy", symbol="A", side="buy", quantity="10", limit_price="100")


def test_shared_funded_manager_rejects_non_owner_before_double_admission(monkeypatch):
    from market_microstructure import order_reservations
    manager = FundedOrderManager(ITEMS, POLICY)
    before, log = manager.snapshot(), manager.export_log()
    original = order_reservations.assess
    barrier, entered = Barrier(2), local()

    def interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        if not getattr(entered, "seen", False):
            entered.seen = True
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(order_reservations, "assess", interleave)

    def attempt(index):
        try:
            manager.submit(dict(ORDER, client_order_id=f"buy-{index}", quantity="6"), now_ns=0, marks=MARKS)
        except RuntimeError as error:
            assert "owner thread" in str(error)
            return "rejected"
        return "approved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, (1, 2))) == ["rejected", "rejected"]
    assert manager.snapshot() == before and manager.export_log() == log
    # The owner still processes commands normally after rejecting foreign callers.
    monkeypatch.setattr(order_reservations, "assess", original)
    assert manager.submit(ORDER, now_ns=0, marks=MARKS)["accepted"]


def test_core_foreign_thread_cannot_apply_fill_but_owner_can():
    manager, venue = OrderManager(ITEMS), SimulatedVenue(ITEMS)
    venue.submit(manager.submit(ORDER))
    fill = venue.execute("buy", "3", "99")
    before = manager.snapshot()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owner thread"):
            pool.submit(manager.apply, fill).result(timeout=5)
    assert manager.snapshot() == before
    manager.apply(fill)
    assert manager.snapshot()["cash_flow"] == "-297"


def test_venue_foreign_thread_cannot_generate_a_fill():
    venue = SimulatedVenue(ITEMS)
    ack = venue.submit(ORDER)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owner thread"):
            pool.submit(venue.execute, "buy", "3", "99").result(timeout=5)
    assert venue.query("buy")["state"] == ack["state"]
    assert venue.execute("buy", "3", "99")["state"]["fills"][0]["execution_id"] == "execution-1"


def test_conflicting_observed_historical_revision_is_rejected_atomically():
    from copy import deepcopy
    manager, venue = OrderManager(ITEMS), SimulatedVenue(ITEMS)
    manager.apply(venue.submit(manager.submit(ORDER)))
    first = venue.execute("buy", "3", "99")
    manager.apply(first)
    second = venue.execute("buy", "2", "100")
    manager.apply(second)
    conflict = deepcopy(second)
    conflict["event_id"] = "historical-conflict"
    conflict["state"]["revision"] = first["state"]["revision"]
    before, log = manager.snapshot(), manager.export_log()
    with pytest.raises(ValueError, match="revision"):
        manager.apply(conflict)
    assert manager.snapshot() == before and manager.export_log() == log


def test_historical_revision_conflict_is_detected_after_sqlite_restart(tmp_path):
    from copy import deepcopy
    path = tmp_path / "orders.sqlite"
    venue = SimulatedVenue(ITEMS)
    with FundedJournal.create(path, ITEMS, POLICY) as store:
        prepared = store.submit("submit", ORDER, now_ns=0, marks=MARKS)
        store.apply("ack", venue.submit(prepared["outgoing"]))
        first = venue.execute("buy", "3", "99")
        store.apply("first", first)
        second = venue.execute("buy", "2", "100")
        store.apply("second", second)
    conflict = deepcopy(second)
    conflict["event_id"] = "historical-conflict"
    conflict["state"]["revision"] = first["state"]["revision"]
    with FundedJournal.open(path) as store:
        before = store.recover()
        with pytest.raises(ValueError, match="revision"):
            store.apply("conflict", conflict)
        assert store.recover() == before


def test_malformed_core_replay_action_has_validation_error():
    with pytest.raises(ValueError, match="operation"):
        OrderManager.replay(ITEMS, [{"action": []}])


def test_memory_budget_stops_new_orders_but_allows_full_fill_cancel_race_and_replay(monkeypatch):
    from market_microstructure import order_protocol
    manager, venue = FundedOrderManager(ITEMS, POLICY), SimulatedVenue(ITEMS)
    outgoing = manager.submit(ORDER, now_ns=0, marks=MARKS)["order"]
    manager.apply(venue.submit(outgoing))
    monkeypatch.setattr(order_protocol, "MAX_EVENTS", 2)
    venue.query("buy")  # Venue also reaches its new-submission budget.
    manager.request_cancel("buy")
    manager.mark_unknown("buy")
    manager.apply(venue.execute("buy", "10", "99"))
    manager.apply(venue.cancel("buy"))
    balances = manager.snapshot()["account"]
    assert (balances["cash"], balances["reserved_cash"], balances["inventory"]["A"]) == ("10", "0", "10")
    assert manager.snapshot()["lifecycle"]["orders"]["buy"]["cancel_status"] == "too_late"
    with pytest.raises(ValueError, match="bound"):
        manager.submit(dict(ORDER, client_order_id="new"), now_ns=0, marks=MARKS)
    with pytest.raises(ValueError, match="bound"):
        venue.submit(dict(ORDER, client_order_id="new"))
    assert FundedOrderManager.replay(manager.export_log()).snapshot() == manager.snapshot()


def test_sqlite_budget_does_not_block_partial_fill_cancel_reconciliation_or_restart(tmp_path, monkeypatch):
    from market_microstructure import order_protocol
    path = tmp_path / "orders.sqlite"
    venue = SimulatedVenue(ITEMS)
    with FundedJournal.create(path, ITEMS, POLICY) as store:
        outgoing = store.submit("submit", ORDER, now_ns=0, marks=MARKS)["outgoing"]
        store.apply("ack", venue.submit(outgoing))
        monkeypatch.setattr(order_protocol, "MAX_EVENTS", 2)
        store.reconcile("query", "buy", venue.query("buy"))
        store.request_cancel("cancel", "buy")
        fill = venue.execute("buy", "3", "99")
        store.apply("fill", fill)
    with FundedJournal.open(path) as store:
        assert store.recover()["snapshot"]["account"]["reserved_cash"] == "700"
        terminal = venue.cancel("buy")
        store.reconcile("terminal", "buy", terminal)
        assert store.apply("fill", fill)["status"] == "duplicate"
        store.apply("fill-redelivery", fill)
        final = store.recover()
        balances = final["snapshot"]["account"]
        assert (balances["cash"], balances["reserved_cash"], balances["inventory"]["A"]) == ("703", "0", "3")
        assert final["reconciliation"] == []
        with pytest.raises(ValueError, match="bound"):
            store.submit("new", dict(ORDER, client_order_id="new"), now_ns=0, marks=MARKS)
        assert store.recover() == final
    with FundedJournal.open(path) as store:
        assert store.recover() == final
