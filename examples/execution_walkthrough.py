"""Invented single-contract IOC orders; hand arithmetic, not strategy performance."""

from market_microstructure.execution import replay_execution

NS = 10**9


def synthetic_inputs():
    return {
        "source_kind": "SYNTHETIC_TEST_ONLY",
        "instrument": {"symbol": "SYNTH-BTC-USDT", "price_tick": "1",
                       "quantity_step": "1", "min_notional": "5"},
        "policy": {"start_ns": 0, "end_ns": 5 * NS, "initial_cash": "1000",
                   "fee_bps": "5", "latency_ns": NS, "max_quote_age_ns": 5 * NS},
        "quotes": [
            {"quote_id": "q0", "timestamp_ns": 0, "available_ns": 0,
             "session_id": "s1", "valid": True, "bids": [["99", "10"]],
             "asks": [["101", "10"]]},
            {"quote_id": "q3", "timestamp_ns": 3 * NS, "available_ns": 3 * NS,
             "session_id": "s1", "valid": True, "bids": [["109", "10"]],
             "asks": [["111", "10"]]},
        ],
        "orders": [
            {"order_id": "open", "decision_ns": 0, "signal_available_ns": 0,
             "expires_ns": 2 * NS, "session_id": "s1", "action": "open",
             "direction": "long", "quantity": "2"},
            {"order_id": "close", "decision_ns": 3 * NS, "signal_available_ns": 3 * NS,
             "expires_ns": 5 * NS, "session_id": "s1", "action": "close",
             "direction": "long", "quantity": "2"},
        ],
    }


def run_demo():
    return replay_execution(synthetic_inputs())


def main():
    result = run_demo()
    print("SYNTHETIC_TEST_ONLY — invented books and manual orders; no market performance")
    print("order  decision(s) arrival(s) quote requested filled unfilled status")
    for row in result["orders"]:
        print(f"{row['order_id']:5} {row['decision_ns']/NS:11.0f} {row['arrival_ns']/NS:10.0f} "
              f"{row['quote_id']:5} {row['requested_quantity']:9} {row['filled_quantity']:6} "
              f"{row['unfilled_quantity']:8} {row['status']} ({row['reason']})")
    print("Hand calculation: 2 * (109 - 101) = 16; fee = (202 + 218) * 5/10000 = 0.21")
    print(f"Final cash: {result['terminal_cash']}; expected: 1015.79000000")
    print(f"Position: {result['terminal_position']['quantity']}; expected: 0")
    print(f"Reconciliation: {result['reconciliation']}")


if __name__ == "__main__":
    main()
