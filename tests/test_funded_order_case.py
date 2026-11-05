from pathlib import Path
import os
import subprocess
import sys

from examples.funded_order_lifecycle import run_case


def test_shared_funding_and_inventory_case_has_independent_hand_answer():
    result = run_case()
    pending = result["pending"]["account"]
    assert (pending["cash"], pending["reserved_cash"], pending["available_cash"]) == ("1000", "1000", "0")
    before = result["before_cancel"]["account"]
    assert (before["cash"], before["reserved_cash"], before["available_cash"]) == ("703", "700", "3")
    after = result["after_cancel"]["account"]
    assert (after["cash"], after["reserved_cash"], after["available_cash"]) == ("503", "0", "503")
    assert result["first_denial"]["reason"] == "insufficient_cash"
    assert result["shared"]["account"]["reserved_cash"] == "500"
    assert result["shared"]["account"]["available_cash"] == "3"
    assert result["oversell"]["reason"] == "insufficient_inventory"
    final = result["final"]["account"]
    assert final["cash"] == final["available_cash"] == "230"  # 1000-3*99-2*100+2*111-5*99.
    assert final["reserved_cash"] == "0"
    assert final["inventory"] == final["available_inventory"] == {"SYNTH-A": "3", "SYNTH-B": "5"}
    assert all(value == "0" for value in final["reserved_inventory"].values())
    assert result["valuation"]["equity"] == "1030"  # cash230 + A3*100 + B5*100.
    assert result["final"]["lifecycle"]["executions"] == 4
    assert result["duplicate"] == "duplicate" and result["replayed"] == result["final"]


def test_funded_demo_runs_outside_checkout_without_files_or_network_setup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, "-B", str(root / "examples/funded_order_lifecycle.py")],
                         cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout
    assert "Final equity=1030" in run.stdout and "replay matches: True" in run.stdout
    assert list(tmp_path.iterdir()) == []
