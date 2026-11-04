"""Shared collateral contention and release with invented BTC/ETH books."""

from copy import deepcopy

from market_microstructure.execution import replay_execution

if __package__:
    from .execution_walkthrough import NS, synthetic_inputs as single_inputs
else:
    from execution_walkthrough import NS, synthetic_inputs as single_inputs


def synthetic_inputs():
    bundle = single_inputs()
    btc = bundle.pop("instrument")
    btc["symbol"] = "SYNTH-BTC-USDT"
    eth = dict(symbol="SYNTH-ETH-USDT", price_tick="1", quantity_step="1", min_notional="5")
    bundle["instruments"] = [btc, eth]
    bundle["policy"].update(fee_bps="0")
    for row in bundle["quotes"] + bundle["orders"]:
        row["symbol"] = btc["symbol"]
    eth_quote = deepcopy(bundle["quotes"][0])
    eth_quote.update(symbol=eth["symbol"], quote_id="eth0",
                     bids=[["9", "100"]], asks=[["11", "50"]])
    bundle["quotes"].append(eth_quote)
    bundle["orders"][0]["quantity"] = "5"
    bundle["orders"][1]["quantity"] = "2"
    eth_order = deepcopy(bundle["orders"][0])
    eth_order.update(order_id="eth_rejected", symbol=eth["symbol"], quantity="50",
                     decision_ns=NS, signal_available_ns=NS, expires_ns=3 * NS)
    retry = deepcopy(eth_order)
    retry.update(order_id="eth_after_close", decision_ns=3 * NS,
                 signal_available_ns=3 * NS, expires_ns=5 * NS)
    bundle["orders"].extend([eth_order, retry])
    return bundle


def main():
    result = replay_execution(synthetic_inputs())
    print("SYNTHETIC_TEST_ONLY — shared collateral; manual orders, no strategy performance")
    print("order             status    equity_before reserved_before equity_projected required headroom")
    for row in result["orders"]:
        risk = row["risk"]
        before = risk["before"]
        projected = risk["projected"] or {}
        print(f"{row['order_id']:17} {row['status']:9} {before['equity']:13} "
              f"{before['reserved_margin']:15} {projected.get('equity', '-'):16} "
              f"{projected.get('required_collateral', '-'):8} "
              f"{projected.get('collateral_headroom', '-')} ({row['reason']})")
    final = result["terminal_account"]
    print(f"Final cash={final['cash']}, equity={final['equity']}, "
          f"reserved={final['reserved_margin']}, gross={final['gross_notional']}")
    print("Expected: cash=1016, equity=993, reserved=853, gross=830; positions: BTC=3, ETH=50")
    print(f"Reconciliation: {result['reconciliation']}")


if __name__ == "__main__":
    main()
