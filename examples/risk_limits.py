"""Artificial order/symbol/portfolio caps and passive holding-limit observations."""

from copy import deepcopy

from market_microstructure.execution import replay_execution
from market_microstructure.execution_monitor import monitor_execution

if __package__:
    from .execution_walkthrough import NS, synthetic_inputs
else:
    from execution_walkthrough import NS, synthetic_inputs


def limited_inputs():
    bundle = synthetic_inputs()
    bundle["policy"]["risk_limits"] = {
        "max_order_notional": "500",
        "max_symbol_gross": {"SYNTH-BTC-USDT": "250"},
        "max_portfolio_gross": "600",
    }
    return bundle


def main():
    print("SYNTHETIC_TEST_ONLY — 담보와 별도로 검사하는 주문·상품·계정 한도")
    for name in ("max_order_notional", "max_symbol_gross", "max_portfolio_gross"):
        bundle = limited_inputs()
        limits = bundle["policy"]["risk_limits"]
        if name == "max_symbol_gross":
            limits[name]["SYNTH-BTC-USDT"] = "180"
        else:
            limits[name] = "180"
        bundle["orders"] = [bundle["orders"][0]]
        order = replay_execution(bundle)["orders"][0]
        risk = order["risk"]
        print(f"\n{name}: {order['status']} / {order['reason']}")
        print(f"  담보 여유={risk['projected']['collateral_headroom']}")
        for check in risk["projected"]["limit_checks"]:
            print(f"  {check['name']}: 판단값={check['value']}, 한도={check['limit']}, "
                  f"여유={check['headroom']}, 통과={check['passed']}")
    bundle = limited_inputs()
    bundle["orders"] = [bundle["orders"][0]]
    bundle["quotes"][1].update(bids=[["149", "10"]], asks=[["151", "10"]])
    recovery = deepcopy(bundle["quotes"][0])
    recovery.update(quote_id="q4", timestamp_ns=4 * NS, available_ns=4 * NS)
    bundle["quotes"].append(recovery)
    observed = monitor_execution(bundle)
    print("\n보유 중 가격 상승 → 상품 노출 한도 초과 → 회복 (자동 조치 없음)")
    for event in observed["risk_events"]:
        print(f"  {event['timestamp_ns']}ns: {event['change']} / {event['to_status']}")
    print(f"원장 검산={observed['execution']['reconciliation']}")


if __name__ == "__main__":
    main()
