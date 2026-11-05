from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue


INSTRUMENTS = {"A": dict(price_tick="1", quantity_step="1")}
POLICY = dict(source_kind=KIND, initial_cash="1000", session_id="s", max_quote_age_ns=10,
              risk_limits=dict(max_order_notional="1000", max_symbol_gross={"A": "1000"},
                               max_portfolio_gross="1000"))
MARKS = {"A": dict(quote_id="q", timestamp_ns=0, available_ns=0, session_id="s", valid=True, price="100")}
ORDER = dict(source_kind=KIND, client_order_id="buy-1", symbol="A", side="buy", quantity="10", limit_price="100")


def submit(store, command_id="submit-1", order=None):
    return store.submit(command_id, ORDER if order is None else order, now_ns=0, marks=MARKS)


def create(path):
    return FundedJournal.create(path, INSTRUMENTS, POLICY)


def balances(store):
    return store.recover()["snapshot"]["account"]


def test_submit_reservation_is_committed_before_outgoing_and_survives_reopen(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        result = submit(store)
        assert result["status"] == "committed" and result["outgoing"] == ORDER
        # An independent reader already sees the reservation before transport.
        with FundedJournal.open(path) as reader:
            assert balances(reader)["reserved_cash"] == "1000"
        recovery = store.recover()
        assert recovery["reconciliation"] == [dict(client_order_id="buy-1", status="pending_new", cancel_status="none")]
    with FundedJournal.open(path) as store:
        assert store.recover() == recovery
        retry = submit(store)
        assert retry["status"] == "duplicate" and retry["outgoing"] is None
        assert retry["result"]["accepted"]
        assert store.recover() == recovery
        result = submit(store, "submit-2", dict(ORDER, client_order_id="buy-2", quantity="1"))
        assert result["outgoing"] is None and result["result"]["reason"] == "insufficient_cash"


def test_not_found_after_restart_is_unknown_not_permission_to_resend(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        outgoing = submit(store)["outgoing"]
    venue = SimulatedVenue(INSTRUMENTS)
    with FundedJournal.open(path) as store:
        result = store.reconcile("query-1", "buy-1", venue.query("buy-1"))
        assert result["result"] == dict(found=False, client_order_id="buy-1", unresolved=True)
        assert balances(store)["reserved_cash"] == "1000"
        assert store.recover()["reconciliation"][0]["status"] == "unknown"
        assert submit(store)["outgoing"] is None
        before = store.recover()
        with pytest.raises(ValueError, match="already registered"):
            submit(store, "dangerous-resend")
        assert store.recover() == before
    # The delayed original submission can still arrive after the absent query.
    venue.submit(outgoing)
    delayed = venue.execute("buy-1", "3", "99")
    with FundedJournal.open(path) as store:
        store.reconcile("query-2", "buy-1", venue.query("buy-1"))
        assert (balances(store)["cash"], balances(store)["reserved_cash"]) == ("703", "700")
        store.apply("late-fill", delayed)
        assert balances(store)["cash"] == "703"


def test_cancel_commit_retry_and_absent_or_live_query_keep_reservations(tmp_path):
    path = tmp_path / "orders.sqlite"
    venue = SimulatedVenue(INSTRUMENTS)
    with create(path) as store:
        store.apply("ack", venue.submit(submit(store)["outgoing"]))
        cancel = store.request_cancel("cancel-1", "buy-1")
        assert cancel["outgoing"]["action"] == "cancel"
    with FundedJournal.open(path) as store:
        assert store.request_cancel("cancel-1", "buy-1")["outgoing"] is None
        store.reconcile("query-missing", "buy-1", None)
        assert store.recover()["reconciliation"][0]["cancel_status"] == "unknown"
        store.reconcile("query-open", "buy-1", venue.query("buy-1"))
        assert store.recover()["reconciliation"][0]["cancel_status"] == "unknown"
        assert balances(store)["reserved_cash"] == "1000"
        venue.execute("buy-1", "3", "99")
        venue.cancel("buy-1")
        store.reconcile("query-canceled", "buy-1", venue.query("buy-1"))
        assert balances(store)["available_cash"] == "703"
        assert store.recover()["reconciliation"] == []


def test_full_fill_wins_persisted_cancel_then_recovers_without_duplicate_accounting(tmp_path):
    path = tmp_path / "orders.sqlite"
    venue = SimulatedVenue(INSTRUMENTS)
    with create(path) as store:
        store.apply("ack", venue.submit(submit(store)["outgoing"]))
        store.request_cancel("cancel-1", "buy-1")
    fill = venue.execute("buy-1", "10", "99")
    with FundedJournal.open(path) as store:
        store.apply("cancel-too-late", venue.cancel("buy-1"))
        store.apply("late-fill", fill)
        assert balances(store)["cash"] == "10"
        assert balances(store)["reserved_cash"] == "0"
        assert store.recover()["snapshot"]["lifecycle"]["orders"]["buy-1"]["cancel_status"] == "too_late"
    with FundedJournal.open(path) as store:
        assert store.apply("late-fill", fill)["status"] == "duplicate"
        assert store.apply("new-delivery-id", fill)["result"] == "duplicate"
        assert balances(store)["cash"] == "10"


def test_persisted_sell_reservation_prevents_oversell_after_restart(tmp_path):
    path = tmp_path / "orders.sqlite"
    venue = SimulatedVenue(INSTRUMENTS)
    with create(path) as store:
        venue.submit(submit(store)["outgoing"])
        store.apply("fill-buy", venue.execute("buy-1", "10", "99"))
        sale = submit(store, "submit-sale", dict(ORDER, client_order_id="sell-1", side="sell", quantity="8"))
    with FundedJournal.open(path) as store:
        assert balances(store)["available_inventory"]["A"] == "2"
        denial = submit(store, "oversell", dict(ORDER, client_order_id="sell-2", side="sell", quantity="3"))
        assert denial["result"]["reason"] == "insufficient_inventory"
        venue.submit(sale["outgoing"])
        store.apply("fill-sale", venue.execute("sell-1", "3", "101"))
        assert balances(store)["cash"] == "313"
        assert balances(store)["reserved_inventory"]["A"] == "5"


@pytest.mark.parametrize("conflict", ["order", "clock", "action"])
def test_same_command_id_with_different_request_preserves_database(conflict, tmp_path):
    with create(tmp_path / "orders.sqlite") as store:
        submit(store)
        before = store.recover()
        with pytest.raises(ValueError, match="Conflicting request"):
            if conflict == "order":
                submit(store, order=dict(ORDER, quantity="9"))
            elif conflict == "clock":
                store.submit("submit-1", ORDER, now_ns=1, marks=MARKS)
            else:
                store.mark_unknown("submit-1", "buy-1")
        assert store.recover() == before


def test_corrupt_report_or_wrong_query_order_has_no_persisted_effect(tmp_path):
    venue = SimulatedVenue(INSTRUMENTS)
    with create(tmp_path / "orders.sqlite") as store:
        venue.submit(submit(store, order=dict(ORDER, quantity="5"))["outgoing"])
        venue.submit(submit(store, "submit-2", dict(ORDER, client_order_id="buy-2", quantity="5"))["outgoing"])
        fill = venue.execute("buy-2", "1", "100")
        before = store.recover()
        with pytest.raises(ValueError, match="another order"):
            store.reconcile("wrong-query", "buy-1", fill)
        assert store.recover() == before
        corrupt = deepcopy(fill)
        corrupt["state"]["fills"][0]["price"] = "101"
        with pytest.raises(ValueError):
            store.apply("bad-fill", corrupt)
        assert store.recover() == before


@pytest.mark.parametrize("after_snapshot", [False, True])
def test_storage_exception_rolls_back_command_and_snapshot(tmp_path, monkeypatch, after_snapshot):
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        before = store.recover()
        original = store._write_snapshot

        def fail(snapshot):
            if after_snapshot:
                original(snapshot)
            raise RuntimeError("injected storage failure")

        monkeypatch.setattr(store, "_write_snapshot", fail)
        with pytest.raises(RuntimeError):
            submit(store)
        assert store.recover() == before
    with FundedJournal.open(path) as store:
        assert submit(store)["outgoing"] == ORDER


@pytest.mark.parametrize("boundary", ["before_snapshot", "before_commit", "after_commit"])
@pytest.mark.parametrize("operation", ["submit", "fill", "cancel_confirmation"])
def test_abrupt_process_exit_around_commit_has_no_partial_reservation_or_resend(tmp_path, boundary, operation):
    path = tmp_path / "orders.sqlite"
    report = None
    venue = SimulatedVenue(INSTRUMENTS)
    with create(path) as store:
        if operation != "submit":
            store.apply("ack", venue.submit(submit(store)["outgoing"]))
            report = venue.execute("buy-1", "3", "99")
        if operation == "cancel_confirmation":
            store.apply("fill", report)
            store.request_cancel("cancel", "buy-1")
            report = venue.cancel("buy-1")
        initial = store.recover()
    script = """
import json, os, sys
from market_microstructure.funded_journal import FundedJournal
store = FundedJournal.open(sys.argv[1])
boundary = sys.argv[2]
if boundary == 'before_snapshot':
    store._write_snapshot = lambda snapshot: os._exit(71)
elif boundary == 'before_commit':
    original = store._write_snapshot
    def crash(snapshot):
        original(snapshot)
        os._exit(71)
    store._write_snapshot = crash
else:
    class CrashAfterCommit:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, name): return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            os._exit(71)
    store._connection = CrashAfterCommit(store._connection)
order, marks, report = map(json.loads, sys.argv[3:6])
if report is None:
    store.submit('submit-1', order, now_ns=0, marks=marks)
else:
    store.apply('interrupted-response', report)
raise AssertionError('unreachable')
"""
    run = subprocess.run([sys.executable, "-B", "-c", script, str(path), boundary,
                          json.dumps(ORDER), json.dumps(MARKS), json.dumps(report)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert run.returncode == 71, run.stderr
    committed = boundary == "after_commit"
    with FundedJournal.open(path) as store:
        assert store.recover()["committed_commands"] == initial["committed_commands"] + int(committed)
        if not committed:
            assert store.recover() == initial
        if operation == "submit":
            assert balances(store)["reserved_cash"] == ("1000" if committed else "0")
            retry = submit(store)
        else:
            if committed:
                assert balances(store)["cash"] == "703"
                assert balances(store)["reserved_cash"] == ("700" if operation == "fill" else "0")
            retry = store.apply("interrupted-response", report)
        assert retry["status"] == ("duplicate" if committed else "committed")
        assert retry["outgoing"] == (ORDER if operation == "submit" and not committed else None)


def test_abrupt_exit_after_venue_fill_recovers_by_query_and_never_reissues_order(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path):
        pass
    script = """
import json, os, sys
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue
store = FundedJournal.open(sys.argv[1])
order, marks, instruments = map(json.loads, sys.argv[2:])
outgoing = store.submit('submit-1', order, now_ns=0, marks=marks)['outgoing']
venue = SimulatedVenue(instruments)
venue.submit(outgoing)
venue.execute('buy-1', '3', '99')
# A query snapshot is passed to the parent as the independent venue fixture.
print(json.dumps(venue.query('buy-1')), flush=True)
os._exit(72)
"""
    run = subprocess.run([sys.executable, "-B", "-c", script, str(path), json.dumps(ORDER),
                          json.dumps(MARKS), json.dumps(INSTRUMENTS)], cwd=tmp_path,
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 72, run.stderr
    query = json.loads(run.stdout)
    with FundedJournal.open(path) as store:
        assert balances(store)["reserved_cash"] == "1000"
        assert submit(store)["outgoing"] is None
        store.reconcile("query-fill", "buy-1", query)
        assert (balances(store)["cash"], balances(store)["reserved_cash"], balances(store)["available_cash"]) == ("703", "700", "3")
    with FundedJournal.open(path) as store:
        assert store.reconcile("query-fill", "buy-1", query)["status"] == "duplicate"
        assert balances(store)["cash"] == "703"


def test_two_handles_see_fresh_commits_and_do_not_return_duplicate_dispatch(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path) as first, FundedJournal.open(path) as second:
        assert submit(first)["outgoing"] == ORDER
        assert submit(second)["outgoing"] is None
        assert first.recover() == second.recover()


def test_concurrent_writers_cannot_reserve_the_same_cash_twice(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path):
        pass
    barrier = Barrier(2)

    def attempt(index):
        with FundedJournal.open(path) as store:
            barrier.wait(timeout=5)
            return submit(store, f"command-{index}", dict(ORDER, client_order_id=f"buy-{index}", quantity="6"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(row["result"]["accepted"] for row in results) == 1
    with FundedJournal.open(path) as store:
        assert store.recover()["committed_commands"] == 2
        assert balances(store)["reserved_cash"] == "600"


@pytest.mark.parametrize("corruption", ["snapshot", "request", "result", "gap", "code", "policy", "source"])
def test_inconsistent_database_is_rejected_not_silently_repaired(tmp_path, corruption):
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        submit(store)
    with sqlite3.connect(path) as connection:
        if corruption == "snapshot":
            connection.execute("UPDATE snapshot SET payload='{}'")
        elif corruption in {"request", "result"}:
            connection.execute(f"UPDATE commands SET {corruption}='{{}}'")
        elif corruption == "gap":
            connection.execute("UPDATE commands SET sequence=2")
        else:
            metadata = json.loads(connection.execute("SELECT payload FROM metadata").fetchone()[0])
            if corruption == "code":
                metadata["definition_sha256"] = "changed"
            elif corruption == "source":
                metadata["source_kind"] = "REAL_OBSERVED"
            else:
                metadata["initial"]["policy"]["risk_limits"]["max_order_notional"] = "999"
            connection.execute("UPDATE metadata SET payload=?", (json.dumps(metadata, sort_keys=True, separators=(",", ":")),))
    with pytest.raises(ValueError):
        FundedJournal.open(path)


def test_missing_existing_symlink_and_invalid_policy_paths_are_safe(tmp_path):
    path = tmp_path / "orders.sqlite"
    path.write_text("keep")
    with pytest.raises(FileExistsError):
        create(path)
    assert path.read_text() == "keep"
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        FundedJournal.open(missing)
    assert not missing.exists()
    with pytest.raises(ValueError):
        FundedJournal.create(missing, INSTRUMENTS, dict(POLICY, source_kind="REAL_OBSERVED"))
    assert not missing.exists()
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        FundedJournal.open(alias)


def test_denied_decision_and_input_isolation_survive_reopen(tmp_path):
    path = tmp_path / "orders.sqlite"
    order = deepcopy(ORDER)
    with create(path) as store:
        outgoing = submit(store, order=order)["outgoing"]
        order["quantity"] = "1"
        outgoing["quantity"] = "1"
        rejected = submit(store, "denied", dict(ORDER, client_order_id="buy-2", quantity="1"))
        assert not rejected["result"]["accepted"] and rejected["outgoing"] is None
    with FundedJournal.open(path) as store:
        assert balances(store)["reserved_cash"] == "1000"
        assert submit(store, "denied", dict(ORDER, client_order_id="buy-2", quantity="1"))["outgoing"] is None
        assert store.recover()["committed_commands"] == 2


def test_code_definition_change_is_rejected_by_live_handle_and_open(tmp_path, monkeypatch):
    from market_microstructure import funded_journal
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        monkeypatch.setattr(funded_journal, "_definition_sha", lambda: "changed")
        with pytest.raises(ValueError, match="definition changed"):
            store.recover()
        with pytest.raises(ValueError, match="definition changed"):
            FundedJournal.open(path)


def test_database_admission_bound_allows_duplicate_lookup_and_queries(tmp_path, monkeypatch):
    from market_microstructure import order_protocol
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        submit(store)
        monkeypatch.setattr(order_protocol, "MAX_EVENTS", 1)
        before = store.recover()
        assert submit(store)["status"] == "duplicate"
        with pytest.raises(ValueError, match="bound"):
            submit(store, "new-submit", dict(ORDER, client_order_id="buy-2"))
        assert store.recover() == before
        store.reconcile("new-query", "buy-1", None)
        assert balances(store)["reserved_cash"] == "1000"


def test_assessment_and_replayed_decision_clock_survive_reopen(tmp_path):
    path = tmp_path / "orders.sqlite"
    with create(path) as store:
        result = store.submit("stale-decision", ORDER, now_ns=11, marks=MARKS)
        assert result["result"]["reason"] == "portfolio_mark_unavailable"
    with FundedJournal.open(path) as store:
        before = store.recover()
        with pytest.raises(ValueError, match="backwards"):
            submit(store)
        with pytest.raises(ValueError, match="backwards"):
            store.assess(now_ns=0, marks=MARKS)
        observation = store.assess(now_ns=11, marks={})
        assert observation["equity"] == "1000"
        assert store.recover() == before


def test_absent_query_for_unknown_id_does_not_create_order_or_release_funds(tmp_path):
    with create(tmp_path / "orders.sqlite") as store:
        submit(store)
        before = store.recover()
        with pytest.raises(ValueError, match="unregistered"):
            store.reconcile("bad-query", "unregistered", None)
        assert store.recover() == before
