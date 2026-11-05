"""Committed-before-send, absent query, missed fills and cancel recovery."""

import argparse
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue


def _inputs():
    instruments = {"SYNTH-A": dict(price_tick="1", quantity_step="1")}
    policy = dict(source_kind=KIND, initial_cash="1000", session_id="demo-session", max_quote_age_ns=10,
                  risk_limits=dict(max_order_notional="1000", max_symbol_gross={"SYNTH-A": "1000"},
                                   max_portfolio_gross="1000"))
    marks = {"SYNTH-A": dict(quote_id="mark", timestamp_ns=0, available_ns=0,
                             session_id="demo-session", valid=True, price="100")}
    order = dict(source_kind=KIND, client_order_id="buy-a", symbol="SYNTH-A", side="buy",
                 quantity="10", limit_price="100")
    return instruments, policy, marks, order


def run_case():
    instruments, policy, marks, order = _inputs()
    venue = SimulatedVenue(instruments)
    with TemporaryDirectory(prefix="synthetic-funded-journal-") as directory:
        path = Path(directory) / "orders.sqlite"
        with FundedJournal.create(path, instruments, policy) as store:
            prepared = store.submit("submit-a", order, now_ns=0, marks=marks)
            committed = store.recover()
        # Restart before a send outcome is known: no automatic dispatch replay.
        with FundedJournal.open(path) as store:
            duplicate = store.submit("submit-a", order, now_ns=0, marks=marks)
            absent = store.reconcile("query-absent", "buy-a", venue.query("buy-a"))
            unknown = store.recover()
        # The ORIGINAL delayed request arrives after that query; no second submit.
        delayed_ack = venue.submit(prepared["outgoing"])
        first_trade = venue.execute("buy-a", "3", "99")
        with FundedJournal.open(path) as store:
            store.reconcile("query-fill", "buy-a", venue.query("buy-a"))
            partial = store.recover()
            store.request_cancel("cancel-a", "buy-a")
        second_trade = venue.execute("buy-a", "2", "100")
        venue.cancel("buy-a")  # Cancel response is lost as well.
        with FundedJournal.open(path) as store:
            store.reconcile("query-terminal", "buy-a", venue.query("buy-a"))
            store.apply("delayed-ack", delayed_ack)
            store.apply("delayed-first", first_trade)
            store.apply("delayed-second", second_trade)
            final = store.recover()
        with FundedJournal.open(path) as store:
            recovered = store.recover()
    return dict(source_kind=KIND, committed=committed, duplicate=duplicate, absent=absent,
                unknown=unknown, partial=partial, final=final, recovered=recovered)


_CLIENT_WORKER = """
import json, sys
from market_microstructure.funded_journal import FundedJournal
instruments, policy = json.loads(sys.argv[2])
with FundedJournal.create(sys.argv[1], instruments, policy) as store:
    for line in sys.stdin:
        request = json.loads(line)
        method = request.pop('method')
        if method not in ('submit', 'reconcile', 'request_cancel', 'recover'):
            raise ValueError('Unsupported demo request')
        print(json.dumps(getattr(store, method)(**request)), flush=True)
"""


def _request(process, method, **arguments):
    process.stdin.write(json.dumps(dict(method=method, **arguments)) + "\n")
    process.stdin.flush()
    if not select.select([process.stdout], [], [], 10)[0]:
        raise TimeoutError("Demo client did not respond within 10 seconds")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("Demo client exited without returning a result")
    return json.loads(line)


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _execution_facts(reports):
    facts = {}
    for report in reports:
        spec = report["state"]["order"]
        for fill in report["state"]["fills"]:
            fact = dict(client_order_id=spec["client_order_id"], symbol=spec["symbol"], side=spec["side"], **fill)
            key = fill["execution_id"]
            _require(key not in facts or facts[key] == fact, "Conflicting oracle execution")
            facts[key] = fact
    return facts


def _venue_ledger(instruments, initial_cash, facts):
    # Independent arithmetic on venue-side facts; no OMS account/replay functions.
    with localcontext() as context:
        context.prec = 128
        cash = Decimal(initial_cash)
        inventory = {symbol: Decimal(0) for symbol in instruments}
        for fact in facts.values():
            signed = Decimal(fact["quantity"]) * (1 if fact["side"] == "buy" else -1)
            cash -= signed * Decimal(fact["price"])
            inventory[fact["symbol"]] += signed
        return dict(cash=format(cash, "f"), inventory={s: format(q, "f") for s, q in inventory.items()},
                    executions=len(facts))


def _assert_ledger(recovery, expected):
    snapshot = recovery["snapshot"]
    balances = snapshot["account"]
    _require(Decimal(balances["cash"]) == Decimal(expected["cash"]), "Cash differs from independent venue ledger")
    _require({s: Decimal(q) for s, q in balances["inventory"].items()}
             == {s: Decimal(q) for s, q in expected["inventory"].items()}, "Inventory differs from venue ledger")
    _require(snapshot["lifecycle"]["executions"] == expected["executions"], "Execution count differs")
    _require(Decimal(balances["reserved_cash"]) == 0
             and all(Decimal(q) == 0 for q in balances["reserved_inventory"].values()), "Terminal reservations remain")
    _require(balances["cash"] == balances["available_cash"]
             and balances["inventory"] == balances["available_inventory"], "Available balances differ")


def run_crash_case():
    """POSIX demo: venue stays in parent; only the journal client is SIGKILLed."""
    if os.name != "posix":
        raise RuntimeError("The forced-kill demo requires POSIX pipes and SIGKILL")
    instruments, policy, marks, order = _inputs()
    venue = SimulatedVenue(instruments)
    with TemporaryDirectory(prefix="synthetic-client-crash-") as directory:
        path = Path(directory) / "orders.sqlite"
        process = subprocess.Popen([sys.executable, "-u", "-B", "-c", _CLIENT_WORKER, str(path),
                                    json.dumps([instruments, policy])],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            prepared = _request(process, "submit", command_id="submit-a", order=order, now_ns=0, marks=marks)
            committed = _request(process, "recover")
            duplicate = _request(process, "submit", command_id="submit-a", order=order, now_ns=0, marks=marks)
            _require(duplicate["outgoing"] is None, "Repeated command returned an outgoing order")
            absent = _request(process, "reconcile", command_id="query-absent", client_order_id="buy-a",
                              report=venue.query("buy-a"))
            unknown = _request(process, "recover")
            _require(unknown["snapshot"]["account"]["reserved_cash"] == "1000"
                     and unknown["snapshot"]["lifecycle"]["orders"]["buy-a"]["status"] == "unknown",
                     "Absent query released or confirmed the unanswered order")
            delayed_ack = venue.submit(prepared["outgoing"])  # Deliver the original delayed request once.
            venue_submissions = 1
            first_trade = venue.execute("buy-a", "3", "99")
            _request(process, "reconcile", command_id="query-fill", client_order_id="buy-a", report=venue.query("buy-a"))
            partial = _request(process, "recover")
            cancel = _request(process, "request_cancel", command_id="cancel-a", client_order_id="buy-a")
            _require(cancel["outgoing"]["action"] == "cancel", "Cancel was not committed before dispatch")
            second_trade = venue.execute("buy-a", "2", "100")  # Fill beats the queued cancel.
            venue.cancel("buy-a")  # Withhold both this response and the second trade.
            before_crash = _request(process, "recover")
            balances = before_crash["snapshot"]["account"]
            _require((balances["cash"], balances["reserved_cash"], balances["inventory"]["SYNTH-A"])
                     == ("703", "700", "3"), "Cancel race changed the unconfirmed local balances")
            _require(before_crash["snapshot"]["lifecycle"]["orders"]["buy-a"]["cancel_status"] == "pending",
                     "Cancel was confirmed before its response arrived")
            process.kill()
            _, stderr = process.communicate(timeout=10)
            _require(process.returncode == -signal.SIGKILL, f"Unexpected client exit: {stderr}")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if not pipe.closed:
                    pipe.close()
        with FundedJournal.open(path) as store:
            restart = store.recover()
            _require(restart == before_crash, "Restart changed committed state")
            _require(store.submit("submit-a", order, now_ns=0, marks=marks)["outgoing"] is None,
                     "Restart reissued the original order")
            store.reconcile("query-terminal", "buy-a", venue.query("buy-a"))
            terminal = store.recover()["snapshot"]
            for index, report in enumerate((delayed_ack, first_trade, second_trade)):
                command_id = f"late-{index}"
                store.apply(command_id, report)
                _require(store.apply(command_id, report)["outgoing"] is None, "Duplicate command returned outgoing")
                store.apply(f"duplicate-event-{index}", report)
                _require(store.recover()["snapshot"] == terminal, "Redelivery changed order/account state")
            final = store.recover()
        with FundedJournal.open(path) as store:
            recovered = store.recover()
        venue_facts = _execution_facts((first_trade, second_trade))
        oracle = _venue_ledger(instruments, policy["initial_cash"], venue_facts)
        _assert_ledger(final, oracle)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as reader:
            requests = [json.loads(row[0]) for row in reader.execute("SELECT request FROM commands ORDER BY sequence")]
        stored_facts = _execution_facts([r["report"] for r in requests if r.get("report") is not None])
        _require(stored_facts == venue_facts, "Persisted execution IDs/facts differ from venue")
        _require(final == recovered and not final["reconciliation"], "Final recovery did not reconcile")
        _require(final["snapshot"]["lifecycle"]["orders"]["buy-a"]["cancel_status"] == "canceled",
                 "Cancel response did not resolve the pending cancel")
    return dict(source_kind=KIND, committed=committed, duplicate=duplicate, absent=absent, unknown=unknown,
                partial=partial, before_crash=before_crash, restart=restart, final=final, recovered=recovered,
                client_pid=process.pid, client_exit_code=process.returncode, venue_submissions=venue_submissions,
                redelivery_unchanged=True, oracle=oracle, execution_ids_match=True)


def _print_phase(label, recovery):
    account = recovery["snapshot"]["account"]
    order = recovery["snapshot"]["lifecycle"]["orders"]["buy-a"]
    print(f"{label}: cash={account['cash']}, reserved={account['reserved_cash']}, "
          f"available={account['available_cash']}; order={order['status']}; cancel={order['cancel_status']}; "
          f"filled={order['filled_quantity']}; position={account['inventory']}; "
          f"query targets={len(recovery['reconciliation'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crash", action="store_true", help="kill the client process and audit against the surviving venue")
    arguments = parser.parse_args()
    result = run_crash_case() if arguments.crash else run_case()
    print("SYNTHETIC_TEST_ONLY — SQLite command/reservation recovery; no transport retries")
    print("Local checkpoints: unreceived venue fills appear only after query reconciliation")
    for label in ("committed", "unknown", "partial"):
        _print_phase(label, result[label])
    if arguments.crash:
        _print_phase("before_crash", result["before_crash"])
        print(f"Client forced exit={result['client_exit_code']}; venue submissions={result['venue_submissions']}")
        _print_phase("restart", result["restart"])
    _print_phase("final", result["final"])
    _print_phase("recovered", result["recovered"])
    print(f"Repeated submit: {result['duplicate']['status']}, outgoing={result['duplicate']['outgoing']}")
    print(f"Absent query unresolved: {result['absent']['result']['unresolved']}")
    print(f"Final inventory={result['final']['snapshot']['account']['inventory']}")
    print(f"Reopened SQLite snapshot matches: {result['recovered'] == result['final']}")
    if arguments.crash:
        print("Independent venue ledger matches: True")


if __name__ == "__main__":
    main()
