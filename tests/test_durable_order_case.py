from pathlib import Path
import os
import subprocess
import sys

from examples.durable_order_recovery import run_case


def test_durable_case_absent_query_then_delayed_original_submission_and_missed_fills():
    result = run_case()
    assert result["committed"]["snapshot"]["account"]["reserved_cash"] == "1000"
    assert result["duplicate"]["status"] == "duplicate" and result["duplicate"]["outgoing"] is None
    assert result["absent"]["result"]["unresolved"]
    unknown = result["unknown"]["snapshot"]
    assert unknown["lifecycle"]["orders"]["buy-a"]["status"] == "unknown"
    assert unknown["account"]["reserved_cash"] == "1000"
    partial = result["partial"]["snapshot"]["account"]
    assert (partial["cash"], partial["reserved_cash"], partial["available_cash"]) == ("703", "700", "3")
    final = result["final"]["snapshot"]
    assert final["account"]["cash"] == final["account"]["available_cash"] == "503"  # 1000-3*99-2*100.
    assert final["account"]["reserved_cash"] == "0"
    assert final["account"]["inventory"] == {"SYNTH-A": "5"}
    assert final["lifecycle"]["orders"]["buy-a"]["cancel_status"] == "canceled"
    assert final["lifecycle"]["executions"] == 2
    assert result["final"]["reconciliation"] == []
    assert result["recovered"] == result["final"]


def test_durable_case_command_runs_outside_checkout_and_cleans_databases(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, TMPDIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    run = subprocess.run([sys.executable, "-B", str(root / "examples/durable_order_recovery.py")],
                         cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "final: cash=503, reserved=0, available=503" in run.stdout
    assert "outgoing=None" in run.stdout and "snapshot matches: True" in run.stdout
    assert list(tmp_path.iterdir()) == []
