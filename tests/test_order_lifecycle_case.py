from pathlib import Path
import os
import subprocess
import sys

from examples.order_lifecycle import run_case


def test_case_lost_responses_cancel_race_and_replay_hand_answer():
    result = run_case()
    assert result["absent_query"] is None
    assert result["unknown"]["orders"]["buy-1"]["needs_reconciliation"]
    assert result["reconciled"]["orders"]["buy-1"]["filled_quantity"] == "3"
    final = result["final"]
    assert (final["orders"]["buy-1"]["status"], final["orders"]["buy-1"]["cancel_status"]) == ("canceled", "canceled")
    assert final["net_filled_quantity"] == {"SYNTH-BTC": "5"}
    assert final["cash_flow"] == "-497" and final["executions"] == 2
    assert result["delayed_outcomes"] == ["stale"] * 3
    assert result["duplicate"] == "duplicate"
    assert result["recovered"] == final


def test_demo_runs_outside_checkout_without_creating_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/order_lifecycle.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "cash flow=-497" in run.stdout and "replay matches: True" in run.stdout
    assert list(tmp_path.iterdir()) == []
