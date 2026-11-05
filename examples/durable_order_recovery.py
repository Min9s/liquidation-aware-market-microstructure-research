"""Committed-before-send, absent query, missed fills and cancel recovery."""

from pathlib import Path
from tempfile import TemporaryDirectory

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue


def run_case():
    instruments = {"SYNTH-A": dict(price_tick="1", quantity_step="1")}
    policy = dict(source_kind=KIND, initial_cash="1000", session_id="demo-session", max_quote_age_ns=10,
                  risk_limits=dict(max_order_notional="1000", max_symbol_gross={"SYNTH-A": "1000"},
                                   max_portfolio_gross="1000"))
    marks = {"SYNTH-A": dict(quote_id="mark", timestamp_ns=0, available_ns=0,
                             session_id="demo-session", valid=True, price="100")}
    order = dict(source_kind=KIND, client_order_id="buy-a", symbol="SYNTH-A", side="buy",
                 quantity="10", limit_price="100")
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


def main():
    result = run_case()
    print("SYNTHETIC_TEST_ONLY — SQLite command/reservation recovery; no transport retries")
    for label in ("committed", "unknown", "partial", "final"):
        account = result[label]["snapshot"]["account"]
        print(f"{label}: cash={account['cash']}, reserved={account['reserved_cash']}, "
              f"available={account['available_cash']}; query targets={len(result[label]['reconciliation'])}")
    print(f"Repeated submit: {result['duplicate']['status']}, outgoing={result['duplicate']['outgoing']}")
    print(f"Absent query unresolved: {result['absent']['result']['unresolved']}")
    print(f"Final inventory={result['final']['snapshot']['account']['inventory']}")
    print(f"Reopened SQLite snapshot matches: {result['recovered'] == result['final']}")


if __name__ == "__main__":
    main()
