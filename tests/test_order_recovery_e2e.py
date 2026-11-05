from copy import deepcopy
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def test_lost_responses_cancel_race_real_client_kill_and_independent_ledger():
    from examples.durable_order_recovery import run_crash_case
    result = run_crash_case()
    assert result["client_exit_code"] == -signal.SIGKILL
    assert result["client_pid"] != os.getpid()
    assert result["venue_submissions"] == 1
    assert result["duplicate"]["outgoing"] is None
    assert result["absent"]["result"]["unresolved"]
    before = result["before_crash"]["snapshot"]["account"]
    assert (before["cash"], before["reserved_cash"], before["inventory"]["SYNTH-A"]) == ("703", "700", "3")
    assert result["restart"] == result["before_crash"]
    assert result["redelivery_unchanged"]
    final = result["final"]["snapshot"]
    assert final["account"]["cash"] == final["account"]["available_cash"] == "503"
    assert final["account"]["reserved_cash"] == "0"
    assert final["account"]["inventory"] == {"SYNTH-A": "5"}
    assert final["lifecycle"]["executions"] == 2
    assert final["lifecycle"]["orders"]["buy-a"]["cancel_status"] == "canceled"
    assert result["final"]["reconciliation"] == []
    assert result["oracle"] == dict(cash="503", inventory={"SYNTH-A": "5"}, executions=2)
    assert result["execution_ids_match"] and result["recovered"] == result["final"]


def test_crash_demo_is_one_command_and_cleans_its_temporary_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/durable_order_recovery.py"), "--crash"],
                         cwd=tmp_path, env=dict(os.environ, TMPDIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "Client forced exit=-9" in run.stdout
    assert "Independent venue ledger matches: True" in run.stdout
    assert "final: cash=503, reserved=0, available=503" in run.stdout
    before = next(line for line in run.stdout.splitlines() if line.startswith("before_crash:"))
    restarted = next(line for line in run.stdout.splitlines() if line.startswith("restart:"))
    assert "order=partially_filled; cancel=pending; filled=3; position={'SYNTH-A': '3'}" in before
    assert before.split(": ", 1)[1] == restarted.split(": ", 1)[1]
    final = next(line for line in run.stdout.splitlines() if line.startswith("final:"))
    assert "order=canceled; cancel=canceled; filled=5; position={'SYNTH-A': '5'}" in final
    assert list(tmp_path.iterdir()) == []


def test_independent_oracle_rejects_an_incorrect_account_projection():
    from examples.durable_order_recovery import run_crash_case, _assert_ledger
    result = run_crash_case()
    corrupted = deepcopy(result["final"])
    corrupted["snapshot"]["account"]["cash"] = "504"
    with pytest.raises(AssertionError):
        _assert_ledger(corrupted, result["oracle"])
