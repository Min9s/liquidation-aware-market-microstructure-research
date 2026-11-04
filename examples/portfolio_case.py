"""One frozen synthetic case: reject, release margin, observe loss, close, recover."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from market_microstructure.execution_journal import ExecutionJournal
from market_microstructure.execution_monitor import monitor_execution

if __package__:
    from .portfolio_execution import NS, synthetic_inputs as portfolio_inputs
else:
    from portfolio_execution import NS, synthetic_inputs as portfolio_inputs


def synthetic_inputs():
    bundle = portfolio_inputs()
    bundle["policy"].update(end_ns=8 * NS, risk_limits={
        "max_order_notional": "1000",
        "max_symbol_gross": {"SYNTH-BTC-USDT": "1000", "SYNTH-ETH-USDT": "1000"},
        "max_portfolio_gross": "1000",
    })
    shock = deepcopy(bundle["quotes"][-1])
    shock.update(quote_id="eth_shock5", timestamp_ns=5 * NS, available_ns=5 * NS,
                 bids=[["1", "100"]], asks=[["3", "100"]])
    bundle["quotes"].append(shock)
    eth_close = deepcopy(bundle["orders"][-1])
    eth_close.update(order_id="eth_exit", action="close", decision_ns=5 * NS,
                     signal_available_ns=5 * NS, expires_ns=7 * NS)
    btc_close = deepcopy(bundle["orders"][1])
    btc_close.update(order_id="btc_exit", quantity="3", decision_ns=6 * NS,
                     signal_available_ns=6 * NS, expires_ns=8 * NS)
    bundle["orders"].extend([eth_close, btc_close])
    return bundle


def run_case():
    bundle = synthetic_inputs()
    observed = monitor_execution(bundle)
    with TemporaryDirectory(prefix="synthetic-portfolio-case-") as directory:
        path = Path(directory) / "orders.sqlite"
        with ExecutionJournal.create(path, bundle) as journal:
            events = journal.events()
            for event in events[:3]:
                journal.record(event)
        with ExecutionJournal.open(path) as journal:
            duplicate = journal.record(events[0])["status"]
            for event in events[3:]:
                journal.record(event)
            recovered = journal.recover()
    return dict(source_kind="SYNTHETIC_TEST_ONLY", monitoring=observed,
                recovered=recovered, redelivery_status=duplicate)


def main():
    result = run_case()
    execution = result["monitoring"]["execution"]
    print("SYNTHETIC_TEST_ONLY — frozen manual orders; final loss is accounting, not strategy performance")
    print("Orders (arrival time)")
    for order in execution["orders"]:
        print(f"t={order['arrival_ns']//NS}s {order['order_id']}: "
              f"{order['status']} / {order['reason']} / filled={order['filled_quantity']}")
    print("Risk observations (time)")
    for event in result["monitoring"]["risk_events"]:
        print(f"t={event['timestamp_ns']//NS}s risk {event['change']}: "
              f"{event['to_status']}, headroom={event['account']['collateral_headroom']}")
    print(f"Final cash={execution['terminal_cash']}, realized PnL={execution['realized_pnl']}, "
          f"positions={execution['terminal_positions']}")
    print(f"Redelivery={result['redelivery_status']}, recovered cash={result['recovered']['account']['cash']}")
    print("Hand answer: BTC +40, ETH -500, fees0, cash1000 - 460 = 540")


if __name__ == "__main__":
    main()
