"""Five reproducible synthetic execution scenarios, with no output files or APIs."""

from market_microstructure.execution import replay_execution

if __package__:
    from .execution_walkthrough import synthetic_inputs as single_inputs
    from .portfolio_execution import NS, synthetic_inputs as portfolio_inputs
else:
    from execution_walkthrough import synthetic_inputs as single_inputs
    from portfolio_execution import NS, synthetic_inputs as portfolio_inputs


def scenario_inputs():
    """Return fresh, independent bundles; order choices are fixed artificial inputs."""
    normal = single_inputs()
    partial = single_inputs()
    partial["quotes"][0]["asks"] = [["101", "1"], ["102", "1"]]
    for row in partial["orders"]:
        row["quantity"] = "3"

    stale = portfolio_inputs()
    stale["policy"]["max_quote_age_ns"] = 2 * NS
    stale["orders"] = [stale["orders"][0], stale["orders"][-1]]
    stale["orders"][-1].update(order_id="eth_new_entry", quantity="10")
    # BTC enters at t1. ETH has a fresh t3 quote; held BTC still has only t0.
    stale["quotes"] = [q for q in stale["quotes"] if q["quote_id"] != "q3"]
    stale["quotes"][-1].update(timestamp_ns=3 * NS, available_ns=3 * NS)

    reduce_only = portfolio_inputs()
    reduce_only["policy"].update(initial_cash="5000", max_quote_age_ns=2 * NS)
    reduce_only["orders"] = reduce_only["orders"][:3]
    reduce_only["orders"][2]["order_id"] = "eth_open"

    return {
        "normal": normal,
        "partial": partial,
        "shared_collateral": portfolio_inputs(),
        "stale_held_mark": stale,
        "reduce_only": reduce_only,
    }


def run_scenarios():
    return {name: replay_execution(bundle) for name, bundle in scenario_inputs().items()}


def _display(value):
    return "평가 불가" if value is None else str(value)


def main():
    descriptions = {
        "normal": "정상 진입·청산: 수수료까지 반영한 현금 검산",
        "partial": "깊이 부족: 일부만 체결하고 잔량은 종료",
        "shared_collateral": "공유 담보: ETH 진입 거부 → BTC 일부 청산 → ETH 신규 주문 승인",
        "stale_held_mark": "보유 상품의 가격이 오래됨: 신규 진입 거부",
        "reduce_only": "다른 보유 상품의 평가가 불가능해도 수량 감소 청산 허용",
    }
    print("SYNTHETIC_TEST_ONLY — 모든 가격·주문은 인공 입력이며 전략 성과가 아닙니다.")
    for name, result in run_scenarios().items():
        print(f"\n[{name}] {descriptions[name]}")
        for order in result["orders"]:
            print(f"  {order['order_id']}: {order['status']} / {order['reason']} / "
                  f"요청={order['requested_quantity']} 체결={order['filled_quantity']} "
                  f"미체결={order['unfilled_quantity']}")
            risk = order["risk"]
            if risk and risk["projected"] is not None:
                projected = risk["projected"]
                print(f"    예상 equity={projected['equity']}, "
                      f"필요 담보={projected['required_collateral']}, "
                      f"담보 여유={projected['collateral_headroom']}")
            if risk and risk["before"]["unavailable_symbols"]:
                marks = risk["before"]["marks"]
                problems = [f"{symbol}: {marks[symbol]['reason']}"
                            for symbol in risk["before"]["unavailable_symbols"]]
                print("    평가 불가: " + ", ".join(problems))
        account = result["terminal_account"]
        positions = ", ".join(f"{s}={p['quantity']}" for s, p in result["terminal_positions"].items())
        print(f"  종료 현금={account['cash']}, equity={_display(account['equity'])}, "
              f"예약 담보={account['reserved_margin']}, 보유={positions or '없음'}")
        print(f"  원장 검산={result['reconciliation']}")


if __name__ == "__main__":
    main()
