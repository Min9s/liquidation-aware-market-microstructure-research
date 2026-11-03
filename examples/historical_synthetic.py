"""Entirely synthetic historical-shaped inputs; no file access or real accounts."""

from pprint import pprint

from market_microstructure.historical import replay_history


def synthetic_inputs():
    second = 10**9
    t = 1_800_000_000 * second
    base = {"source_kind": "SYNTHETIC_TEST_ONLY", "source": "synthetic",
            "timestamp": t - 2 * second, "ingested_at": t - second, "timestamp_basis": "exchange"}
    accounts = [dict(base, wallet="synthetic-long", snapshot_id="h-long", scoped_position_count=2,
                     requested_at=t - 3 * second),
                dict(base, wallet="synthetic-short", snapshot_id="h-short", scoped_position_count=1,
                     requested_at=t - 3 * second)]
    positions = [dict(base, wallet="synthetic-long", snapshot_id="h-long", coin="BTC", signed_size="2", liquidation_px="99.9"),
                 dict(base, wallet="synthetic-long", snapshot_id="h-long", coin="ETH", signed_size="1", liquidation_px="90"),
                 dict(base, wallet="synthetic-short", snapshot_id="h-short", coin="BTC", signed_size="-3", liquidation_px="100.25")]
    contexts, books = [], []
    for index in range(3):
        now = t + index * 30 * second
        common = dict(base, timestamp=now, ingested_at=now, timestamp_basis="received", coin="BTC", session_id="s1")
        contexts.append(dict(common, event_id=f"ctx-{index}", mid_px="100", mark_px="100", open_interest="1000"))
        books.append(dict(common, event_id=f"bbo-{index}", bid_px="99", ask_px="101", bid_sz="3", ask_sz="1"))
    policy = {"asset": "BTC", "source": "synthetic", "source_kind": "SYNTHETIC_TEST_ONLY",
              "session_id": "s1", "start_ns": t, "end_ns": t + 60 * second, "step_ns": 30 * second,
              "snapshot_age_ns": 300 * second, "market_age_ns": 5 * second, "tau_bps": 10, "epsilon": 1e-12}
    return {"accounts": accounts, "positions": positions, "contexts": contexts, "bbo": books}, policy


def main():
    data, policy = synthetic_inputs()
    result = replay_history(data, policy)
    print("SYNTHETIC_TEST_ONLY — three decisions, two invented accounts, no market data")
    pprint(result["summary"])


if __name__ == "__main__":
    main()
