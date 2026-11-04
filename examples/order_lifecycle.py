"""Synthetic delayed-report and cancel-race scenario; no external connection."""

from market_microstructure.execution_contracts import KIND
from market_microstructure.order_lifecycle import OrderManager
from market_microstructure.simulated_venue import SimulatedVenue


def run_case():
    instruments = {"SYNTH-BTC": {"price_tick": "1", "quantity_step": "1"}}
    manager, venue = OrderManager(instruments), SimulatedVenue(instruments)
    spec = dict(source_kind=KIND, client_order_id="buy-1", symbol="SYNTH-BTC",
                side="buy", quantity="10", limit_price="100")
    outgoing = manager.submit(spec)
    manager.mark_unknown("buy-1")
    absent_query = venue.query("buy-1")  # Request can still be in transit.
    unknown = manager.snapshot()
    delayed_ack = venue.submit(outgoing)
    delayed_first_trade = venue.execute("buy-1", "3", "99")
    manager.apply(venue.query("buy-1"))  # Recover lost ACK and first fill.
    reconciled = manager.snapshot()
    manager.request_cancel("buy-1")
    delayed_second_trade = venue.execute("buy-1", "2", "100")
    cancel = venue.cancel("buy-1")
    manager.apply(cancel)  # Cancel response overtakes both trade responses.
    delayed_outcomes = [manager.apply(report) for report in
                        [delayed_second_trade, delayed_first_trade, delayed_ack]]
    duplicate = manager.apply(cancel)
    final = manager.snapshot()
    recovered = OrderManager.replay(instruments, manager.export_log()).snapshot()
    return dict(source_kind=KIND, absent_query=absent_query, unknown=unknown,
                reconciled=reconciled, final=final, delayed_outcomes=delayed_outcomes,
                duplicate=duplicate, recovered=recovered)


def main():
    result = run_case()
    print("SYNTHETIC_TEST_ONLY — artificial fills; cumulative-state custom protocol")
    print(f"Lost request/ACK: {result['unknown']['orders']['buy-1']['status']}; "
          f"query={result['absent_query']}; no automatic resend")
    print(f"Query reconciliation: filled={result['reconciled']['orders']['buy-1']['filled_quantity']}")
    order = result["final"]["orders"]["buy-1"]
    print(f"Cancel race: {order['status']}; filled={order['filled_quantity']}; "
          f"unfilled={order['remaining_quantity']}; working={order['last_confirmed_working_quantity']}")
    print(f"Delayed reports: {result['delayed_outcomes']}; redelivery={result['duplicate']}")
    print(f"Net filled quantity=5, cash flow={result['final']['cash_flow']} (not PnL)")
    print(f"In-memory log replay matches: {result['recovered'] == result['final']}")


if __name__ == "__main__":
    main()
