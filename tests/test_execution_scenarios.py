from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

from examples.execution_scenarios import run_scenarios


def test_public_scenarios_keep_the_promised_outcomes_and_hand_answers():
    results = run_scenarios()
    assert set(results) == {"normal", "partial", "shared_collateral", "stale_held_mark", "reduce_only"}
    assert Decimal(results["normal"]["terminal_cash"]) == Decimal("1015.79")
    assert results["normal"]["terminal_flat"]
    partial = results["partial"]
    assert partial["orders"][0]["status"] == "partial"
    assert Decimal(partial["orders"][0]["filled_quantity"]) == 2
    assert Decimal(partial["terminal_cash"]) == Decimal("1014.7895")
    shared = results["shared_collateral"]
    assert [r["status"] for r in shared["orders"]] == ["filled", "rejected", "filled", "filled"]
    assert Decimal(shared["orders"][1]["risk"]["projected"]["collateral_headroom"]) == -110
    assert Decimal(shared["terminal_equity"]) == 993
    stale = results["stale_held_mark"]
    assert stale["orders"][-1]["reason"] == "portfolio_mark_unavailable"
    assert stale["terminal_equity"] is None
    reduced = results["reduce_only"]
    assert reduced["orders"][-1]["status"] == "filled"
    assert reduced["orders"][-1]["risk"]["status"] == "reduce_only"
    assert reduced["terminal_equity"] is None
    for result in results.values():
        assert result["source_kind"] == "SYNTHETIC_TEST_ONLY"
        assert result["actual_orders_sent"] == 0 and all(result["reconciliation"].values())
    # A repeated demo must start with new bundles, not consumed depth or account state.
    assert run_scenarios() == results


def test_scenario_command_outside_checkout_leaves_working_directory_empty(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/execution_scenarios.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "[normal]" in run.stdout and "[reduce_only]" in run.stdout
    assert "insufficient_collateral" in run.stdout and "portfolio_mark_unavailable" in run.stdout
    assert list(tmp_path.iterdir()) == []
