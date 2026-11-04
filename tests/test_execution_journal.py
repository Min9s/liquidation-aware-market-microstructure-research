from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from examples.execution_walkthrough import synthetic_inputs as single_inputs
from examples.portfolio_execution import synthetic_inputs as portfolio_inputs
from market_microstructure.execution import replay_execution
from market_microstructure.execution_journal import ExecutionJournal


@pytest.mark.parametrize("factory", [single_inputs, portfolio_inputs])
def test_recovery_at_every_order_boundary_matches_continuous_account(factory, tmp_path):
    bundle = factory()
    direct = replay_execution(bundle)
    for cut in range(len(direct["orders"]) + 1):
        path = tmp_path / f"boundary{cut}.sqlite"
        with ExecutionJournal.create(path, bundle) as store:
            events = store.events()
            for event in events[:cut]:
                assert store.record(event)["status"] == "applied"
        with ExecutionJournal.open(path) as store:
            assert store.recover()["applied_events"] == cut
            before = store.recover()
            for event in events[:cut]:
                assert store.record(event)["status"] == "duplicate"
                assert store.recover() == before
            for event in events[cut:]:
                store.record(event)
            final = store.recover()
        assert final["applied_events"] == len(events)
        assert final["account"]["cash"] == direct["terminal_cash"]
        assert final["account"]["positions"] == direct["terminal_positions"]
        assert final["account"]["fees"] == direct["fees"]
        assert final["account"]["realized_pnl"] == direct["realized_pnl"]
        assert final["account"]["reserved_margin"] == direct["terminal_account"]["reserved_margin"]


@pytest.mark.parametrize("field", ["fee", "quantity", "notional"])
def test_conflicting_redelivery_is_rejected_without_account_change(field, tmp_path):
    with ExecutionJournal.create(tmp_path / "orders.sqlite", single_inputs()) as store:
        event = store.events()[0]
        store.record(event)
        before = store.recover()
        changed = deepcopy(event)
        changed["fill"][field] = "999"
        with pytest.raises(ValueError, match="Conflicting"):
            store.record(changed)
        assert store.recover() == before


def test_out_of_order_or_changed_first_event_never_creates_a_prefix(tmp_path):
    with ExecutionJournal.create(tmp_path / "orders.sqlite", single_inputs()) as store:
        events = store.events()
        with pytest.raises(ValueError, match="next unchanged"):
            store.record(events[1])
        changed = deepcopy(events[0])
        changed["source_kind"] = "REAL_OBSERVED"
        with pytest.raises(ValueError):
            store.record(changed)
        assert store.recover()["applied_events"] == 0
        store.record(events[0])


def test_rejected_order_is_persisted_and_deduplicated_without_fill(tmp_path):
    with ExecutionJournal.create(tmp_path / "orders.sqlite", portfolio_inputs()) as store:
        first, rejected, *_ = store.events()
        assert rejected["fill"] is None
        store.record(first)
        before = store.recover()["account"]
        store.record(rejected)
        assert store.recover()["account"] == before
        assert store.record(rejected)["status"] == "duplicate"
        assert store.recover()["applied_events"] == 2


def test_exception_between_event_and_account_write_rolls_back_both(tmp_path, monkeypatch):
    path = tmp_path / "orders.sqlite"
    with ExecutionJournal.create(path, single_inputs()) as store:
        events = store.events()
        store.record(events[0])
        before = store.recover()

        def failure(state):
            raise RuntimeError("injected storage failure")

        monkeypatch.setattr(store, "_write_account", failure)
        with pytest.raises(RuntimeError):
            store.record(events[1])
        assert store.recover() == before
    with ExecutionJournal.open(path) as store:
        store.record(events[1])
        assert store.recover()["account"]["cash"] == "1015.79000000"


@pytest.mark.parametrize("after_commit", [False, True])
def test_actual_process_exit_has_only_committed_effects(tmp_path, after_commit):
    path = tmp_path / "orders.sqlite"
    with ExecutionJournal.create(path, single_inputs()):
        pass
    script = """
import os, sys
from market_microstructure.execution_journal import ExecutionJournal
store = ExecutionJournal.open(sys.argv[1])
if sys.argv[2] == 'before':
    store._write_account = lambda state: os._exit(19)
store.record(store.events()[0])
os._exit(19)
"""
    run = subprocess.run([sys.executable, "-B", "-c", script, str(path),
                          "after" if after_commit else "before"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 19, run.stderr
    with ExecutionJournal.open(path) as store:
        assert store.recover()["applied_events"] == int(after_commit)
        result = store.record(store.events()[0])
        assert result["status"] == ("duplicate" if after_commit else "applied")
        assert store.recover()["account"]["cash"] == "999.89900000"


def test_two_connections_reread_committed_state_before_duplicate_detection(tmp_path):
    path = tmp_path / "orders.sqlite"
    with ExecutionJournal.create(path, single_inputs()) as first, ExecutionJournal.open(path) as second:
        event = first.events()[0]
        first.record(event)
        assert second.record(event)["status"] == "duplicate"
        second.record(second.events()[1])
        assert first.recover() == second.recover()


@pytest.mark.parametrize("mode", ["state", "event", "gap", "code", "input"])
def test_changed_database_is_rejected_instead_of_repaired(mode, tmp_path):
    path = tmp_path / "orders.sqlite"
    with ExecutionJournal.create(path, single_inputs()) as store:
        store.record(store.events()[0])
    with sqlite3.connect(path) as connection:
        if mode == "state":
            connection.execute("UPDATE account SET payload='{}'")
        elif mode == "event":
            connection.execute("UPDATE events SET payload='{}'")
        elif mode == "gap":
            connection.execute("UPDATE events SET sequence=2")
        else:
            metadata = json.loads(connection.execute("SELECT payload FROM metadata").fetchone()[0])
            if mode == "code":
                metadata["definition_sha256"] = "changed"
            else:
                metadata["bundle"]["policy"]["initial_cash"] = "9999"
            connection.execute("UPDATE metadata SET payload=?", (json.dumps(metadata),))
    with pytest.raises(ValueError):
        ExecutionJournal.open(path)


def test_existing_file_and_real_input_are_not_overwritten_or_created(tmp_path):
    path = tmp_path / "existing.sqlite"
    path.write_text("keep")
    with pytest.raises(FileExistsError):
        ExecutionJournal.create(path, single_inputs())
    assert path.read_text() == "keep"
    bundle = single_inputs()
    bundle["source_kind"] = "REAL_OBSERVED"
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError):
        ExecutionJournal.create(missing, bundle)
    assert not missing.exists()
    with pytest.raises(sqlite3.OperationalError):
        ExecutionJournal.open(missing)
    assert not missing.exists()


def test_demo_runs_outside_checkout_and_cleans_temporary_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    run = subprocess.run([sys.executable, "-B", str(root / "examples/journal_recovery.py")],
                         cwd=tmp_path, env=dict(os.environ, TMPDIR=str(temporary),
                                                PYTHONDONTWRITEBYTECODE="1"),
                         text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert "SYNTHETIC_TEST_ONLY" in run.stdout and "duplicate" in run.stdout
    assert "1015.79000000" in run.stdout
    assert list(temporary.iterdir()) == []
