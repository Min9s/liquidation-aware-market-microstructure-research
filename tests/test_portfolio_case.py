from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

from examples.portfolio_case import run_case


def test_case_hand_accounting_risk_interval_and_recovery():
    result = run_case()
    execution = result["monitoring"]["execution"]
    assert [o["status"] for o in execution["orders"]] == [
        "filled", "rejected", "filled", "filled", "filled", "filled"]
    assert execution["orders"][1]["reason"] == "insufficient_collateral"
    assert Decimal(execution["realized_pnl"]) == -460
    assert Decimal(execution["terminal_cash"]) == 540
    assert Decimal(execution["fees"]) == 0 and execution["terminal_positions"] == {}
    started, resolved = result["monitoring"]["risk_events"]
    assert started["timestamp_ns"] == 5 * 10**9
    assert Decimal(started["account"]["collateral_headroom"]) == -260
    assert resolved["timestamp_ns"] == 6 * 10**9 and resolved["change"] == "resolved"
    assert result["redelivery_status"] == "duplicate"
    assert result["recovered"]["applied_events"] == 6
    assert result["recovered"]["account"]["cash"] == execution["terminal_cash"]
    assert result["recovered"]["account"]["positions"] == execution["terminal_positions"]
    assert result["recovered"]["account"]["realized_pnl"] == execution["realized_pnl"]


def test_case_command_runs_outside_checkout_and_cleans_temporary_files(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, TMPDIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    run = subprocess.run([sys.executable, "-B", str(root / "examples/portfolio_case.py")],
                         cwd=tmp_path, env=env, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout and "540.00000000" in run.stdout
    assert "duplicate" in run.stdout and list(tmp_path.iterdir()) == []
