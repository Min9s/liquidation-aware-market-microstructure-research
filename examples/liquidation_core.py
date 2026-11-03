"""Synthetic Phase 1 arithmetic and availability checks; no market connection."""

import math
from pprint import pprint

from market_microstructure import (
    gravity,
    liquidation_imbalance,
    prepare_exposure,
    select_asof,
)


def main():
    # Entirely invented account IDs, times, sizes and prices.
    rows = [
        {"record_id": "snapshot-1", "source_kind": "SYNTHETIC_TEST_ONLY",
         "source": "synthetic", "entity": "example-account/BTC", "timestamp": 90,
         "ingested_at": 95, "timestamp_basis": "exchange", "complete": True,
         "positions": [("2", "99.9"), ("-3", "100.25"), ("1", None)]},
        {"record_id": "late-snapshot", "source_kind": "SYNTHETIC_TEST_ONLY",
         "source": "synthetic", "entity": "example-account/BTC", "timestamp": 99,
         "ingested_at": 101, "timestamp_basis": "exchange", "complete": True,
         "positions": []},
    ]
    selected = select_asof(rows, decision_ns=100, max_age_ns=20,
                          source_kind="SYNTHETIC_TEST_ONLY", source="synthetic",
                          entity="example-account/BTC")
    print("SYNTHETIC_TEST_ONLY: decision=100 ns; late receipt=101 ns excluded")
    pprint(selected)
    if selected["status"] != "available":
        raise AssertionError("Synthetic example must have an available snapshot")
    prepared = [prepare_exposure(size, liq, "100")
                for size, liq in selected["record"]["positions"]]
    print("One-position preparation (missing liquidation price remains excluded):")
    pprint(prepared)
    up = gravity([(300, 25)], tau_bps=10)
    down = gravity([(200, 10)], tau_bps=10)
    lgi = liquidation_imbalance(up, down)
    # Fixed hand-calculated answers, independent of the result being checked.
    assert math.isclose(up, 24.62549958716964, rel_tol=1e-12)
    assert math.isclose(down, 73.57588823428847, rel_tol=1e-12)
    assert math.isclose(lgi, -0.49846941813201256, rel_tol=1e-12)
    # Cross-check the prepared positions against the explicit numeric pairs.
    for side, expected in (("upper", up), ("lower", down)):
        pairs = [(r["notional"], r["distance_bps"]) for r in prepared
                 if r["status"] == "included" and r["side"] == side]
        assert math.isclose(gravity(pairs, 10), expected, rel_tol=1e-12)
    print(f"Hand calculation verified: up={up:.8f}, down={down:.8f}, LGI={lgi:.8f}")
    print("LGI describes only the included subset; excluded notional=100 is NOT zero exposure.")
    print("Synthetic arithmetic only; no prediction, surface, event study, or trading result.")


if __name__ == "__main__":
    main()
