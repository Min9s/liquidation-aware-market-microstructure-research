"""Transient collateral breach and stale-mark intervals with artificial inputs."""

from copy import deepcopy

from market_microstructure.execution_monitor import monitor_execution

if __package__:
    from .execution_walkthrough import NS, synthetic_inputs
else:
    from execution_walkthrough import NS, synthetic_inputs


def monitoring_inputs():
    drawdown = synthetic_inputs()
    drawdown["policy"].update(initial_cash="204", fee_bps="0")
    drawdown["orders"] = [drawdown["orders"][0]]
    drawdown["quotes"][1].update(bids=[["49", "10"]], asks=[["51", "10"]])
    fresh = deepcopy(drawdown["quotes"][0])
    fresh.update(quote_id="q4", timestamp_ns=4 * NS, available_ns=4 * NS)
    drawdown["quotes"].append(fresh)

    stale = synthetic_inputs()
    stale["policy"].update(fee_bps="0", max_quote_age_ns=NS)
    stale["orders"] = [stale["orders"][0]]
    stale["quotes"] = [stale["quotes"][0], deepcopy(fresh)]
    return {"일시적 담보 부족": drawdown, "보유 가격의 평가 공백": stale}


def seconds(stamp):
    return f"{stamp // NS}.{stamp % NS:09d}"


def main():
    print("SYNTHETIC_TEST_ONLY — 수동 위험 관측; 자동 주문·강제청산 없음")
    for name, bundle in monitoring_inputs().items():
        result = monitor_execution(bundle)
        print(f"\n[{name}]")
        for event in result["risk_events"]:
            account = event["account"]
            headroom = account["collateral_headroom"]
            print(f"  t={seconds(event['timestamp_ns'])}s {event['change']}: "
                  f"{event['from_status']} → {event['to_status']}")
            print(f"    원인={event['trigger']['kind']}/{event['trigger']['identity']}, "
                  f"equity={account['equity']}, 예약={account['reserved_margin']}, "
                  f"담보 여유={headroom if headroom is not None else '평가 불가'}")
        for episode in result["episodes"]:
            print(f"  관측 구간 길이={episode['observed_duration_ns']}ns, "
                  f"종료 시 미해소={episode['open_at_end']}")
        execution = result["execution"]
        print(f"  종료 상태={result['terminal_status']}, 현금={execution['terminal_cash']}, "
              f"원장 검산={execution['reconciliation']}")


if __name__ == "__main__":
    main()
