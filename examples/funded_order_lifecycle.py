"""Shared funding, unknown requests and cancel races; synthetic manual fills."""

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_orders import FundedOrderManager
from market_microstructure.simulated_venue import SimulatedVenue


def run_case():
    instruments = {s: dict(price_tick="1", quantity_step="1") for s in ("SYNTH-A", "SYNTH-B")}
    policy = dict(source_kind=KIND, initial_cash="1000", session_id="demo-session", max_quote_age_ns=10,
                  risk_limits=dict(max_order_notional="1000", max_symbol_gross={s: "1000" for s in instruments},
                                   max_portfolio_gross="1000"))
    marks = {s: dict(quote_id=f"{s}-mark", timestamp_ns=0, available_ns=0,
                     session_id="demo-session", valid=True, price="100") for s in instruments}
    manager, venue = FundedOrderManager(instruments, policy), SimulatedVenue(instruments)

    def admit(client_id, symbol="SYNTH-A", side="buy", quantity="10", limit_price="100"):
        spec = dict(source_kind=KIND, client_order_id=client_id, symbol=symbol, side=side,
                    quantity=quantity, limit_price=limit_price)
        return manager.submit(spec, now_ns=0, marks=marks)

    initial = admit("buy-a")
    manager.mark_unknown("buy-a")
    pending = manager.snapshot()
    first_denial = admit("buy-b", symbol="SYNTH-B", quantity="5")
    venue.submit(initial["order"])
    first_trade = venue.execute("buy-a", "3", "99")
    manager.apply(venue.query("buy-a"))
    manager.request_cancel("buy-a")
    manager.mark_unknown("buy-a")
    second_trade = venue.execute("buy-a", "2", "100")
    before_cancel = manager.snapshot()
    manager.apply(venue.cancel("buy-a"))  # Includes both fills before their delivery.
    manager.apply(first_trade)
    manager.apply(second_trade)
    after_cancel = manager.snapshot()

    # A local denial did not use the client ID at the venue; this retry is allowed.
    second = admit("buy-b", symbol="SYNTH-B", quantity="5")
    venue.submit(second["order"])
    manager.mark_unknown("buy-b")
    shared = manager.snapshot()
    sale = admit("sell-a", side="sell", quantity="5", limit_price="110")
    venue.submit(sale["order"])
    oversell = admit("oversell-a", side="sell", quantity="1")
    manager.apply(venue.execute("sell-a", "2", "111"))
    manager.apply(venue.execute("buy-b", "5", "99"))
    manager.request_cancel("sell-a")
    cancel = venue.cancel("sell-a")
    manager.apply(cancel)
    duplicate = manager.apply(cancel)
    final = manager.snapshot()
    valuation = manager.assess(now_ns=0, marks=marks)
    replayed = FundedOrderManager.replay(manager.export_log()).snapshot()
    return dict(source_kind=KIND, pending=pending, first_denial=first_denial,
                before_cancel=before_cancel, after_cancel=after_cancel, shared=shared,
                oversell=oversell, final=final, valuation=valuation,
                duplicate=duplicate, replayed=replayed)


def main():
    result = run_case()
    print("SYNTHETIC_TEST_ONLY — cash-funded buys, inventory-backed sells, zero fees")
    for label in ("pending", "before_cancel", "after_cancel", "shared", "final"):
        account = result[label]["account"]
        print(f"{label}: cash={account['cash']}, reserved={account['reserved_cash']}, "
              f"available={account['available_cash']}, inventory={account['inventory']}")
    print(f"Competing buy: {result['first_denial']['reason']}; oversell: {result['oversell']['reason']}")
    print(f"Final equity={result['valuation']['equity']}; redelivery={result['duplicate']}")
    print(f"Funded log replay matches: {result['replayed'] == result['final']}")


if __name__ == "__main__":
    main()
