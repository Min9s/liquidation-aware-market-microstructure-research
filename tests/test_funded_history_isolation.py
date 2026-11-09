"""Historical sharing must be immutable; trial state and public values stay detached."""
from copy import deepcopy
import json

import pytest

from market_microstructure.funded_orders import FundedOrderManager
from market_microstructure.oms_operations import OperationsManager, reduce_operations
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.oms_operations import OperationalJournal
from market_microstructure.fix_adapter import FixJournal
from test_funded_orders import INSTRUMENTS, policy, quotes, order, setup_funded, submit
from test_funded_journal import INSTRUMENTS as JOURNAL_INSTRUMENTS, POLICY, MARKS, ORDER


def populated():
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    manager.apply(venue.execute("buy-1", "3", "99"))
    return manager, venue


def test_all_history_actions_are_immutable_records_with_detached_public_exports():
    manager, venue = populated()
    submit(manager, order("denied", quantity="100"))
    manager.request_cancel("buy-1")
    manager.mark_unknown("buy-1")
    manager.apply(venue.cancel("buy-1"))
    manager.apply(venue.query("buy-1"))
    exported = manager.export_log()
    assert {e["action"] for e in exported["events"]} == {
        "submit", "apply", "request_cancel", "mark_unknown"}
    assert all(type(record) is str for record in manager._log)
    assert [json.loads(record) for record in manager._log] == exported["events"]
    trial = deepcopy(manager)
    assert trial._log is not manager._log
    assert all(a is b for a, b in zip(manager._log, trial._log))
    with pytest.raises(TypeError):
        trial._log[0][0] = "x"
    decoded = json.loads(trial._log[0])
    decoded["order"]["quantity"] = "999"
    assert manager.export_log() == exported
    assert trial.export_log() == exported


@pytest.mark.parametrize("surface", ["registry", "policy", "order", "marks", "result", "report", "snapshot", "export"])
def test_supported_input_and_output_values_cannot_alias_history(surface):
    registry, config = deepcopy(INSTRUMENTS), policy()
    manager = FundedOrderManager(registry, config)
    _, venue = setup_funded()
    spec, marks = order(), quotes()
    result = manager.submit(spec, now_ns=0, marks=marks)
    report = venue.submit(result["order"])
    manager.apply(report)
    history, snapshot = manager.export_log(), manager.snapshot()
    if surface == "registry":
        registry["A"]["quantity_step"] = "99"
    elif surface == "policy":
        config["initial_cash"] = "1"
    elif surface == "order":
        spec["quantity"] = "999"
    elif surface == "marks":
        marks["A"]["price"] = "999"
    elif surface == "result":
        result["order"]["quantity"] = "999"
        result["before"]["account"]["reservations"]["fake"] = {}
    elif surface == "report":
        report["state"]["order"]["quantity"] = "999"
        report["state"]["fills"].append({})
    elif surface == "snapshot":
        view = manager.snapshot()
        view["lifecycle"]["orders"]["buy-1"]["order"]["quantity"] = "999"
        view["account"]["reservations"]["buy-1"]["cash"] = "0"
    else:
        view = manager.export_log()
        view["events"][0]["order"]["quantity"] = "999"
        view["events"].clear()
        view["instruments"]["A"]["quantity_step"] = "99"
        view["policy"]["initial_cash"] = "1"
    assert manager.export_log() == history
    assert manager.snapshot() == snapshot


def test_immutable_encoding_preserves_unicode_and_exact_decimal_strings():
    manager, venue = setup_funded()
    spec = order("buy-한글-😀", quantity="1.000", limit_price="100.00000000")
    manager.apply(venue.submit(submit(manager, spec)["order"]))
    manager.apply(venue.execute(spec["client_order_id"], "1.000", "99.00000000"))
    bundle = manager.export_log()
    assert bundle["events"][0]["order"] == spec
    assert bundle["events"][-1]["report"]["state"]["fills"][0]["price"] == "99.00000000"
    replayed = FundedOrderManager.replay(bundle)
    assert replayed.export_log() == bundle and replayed.snapshot() == manager.snapshot()


def test_fork_mutations_and_failed_actions_do_not_change_parent_or_prefix():
    parent, venue = populated()
    before, history = parent.snapshot(), parent.export_log()
    trial = deepcopy(parent)
    trial.request_cancel("buy-1")
    trial.apply(venue.execute("buy-1", "2", "100"))
    assert parent.snapshot() == before
    assert parent.export_log() == history
    assert trial.export_log()["events"][:len(history["events"])] == history["events"]
    failed_before, failed_log = trial.snapshot(), trial.export_log()
    with pytest.raises(ValueError):
        trial.submit(order(), now_ns=0, marks=quotes())
    bad = venue.query("buy-1")
    bad["state"]["fills"][0]["price"] = "101"
    with pytest.raises(ValueError):
        trial.apply(bad)
    assert trial.snapshot() == failed_before
    assert trial.export_log() == failed_log
    assert parent.snapshot() == before and parent.export_log() == history


def test_replay_recreates_the_same_log_without_aliasing_its_input():
    manager, venue = populated()
    manager.request_cancel("buy-1")
    manager.apply(venue.cancel("buy-1"))
    bundle = manager.export_log()
    replayed = FundedOrderManager.replay(bundle)
    assert replayed.export_log() == bundle
    assert replayed.snapshot() == manager.snapshot()
    bundle["events"][0]["order"]["quantity"] = "999"
    bundle["events"].clear()
    assert replayed.export_log() == manager.export_log()
    assert all(type(record) is str for record in replayed._log)


@pytest.mark.parametrize("mode", ["RECOVERY", "RESTRICTED"])
def test_blocked_operational_buy_keeps_funded_working_state_and_history(mode):
    manager = OperationsManager(INSTRUMENTS, policy())
    reduce_operations(manager, dict(action="control", mode=mode, now_ns=0, reason="test"))
    parent = manager.funded
    before, history = parent.snapshot(), parent.export_log()
    result = reduce_operations(manager, dict(action="submit", order=order(), now_ns=1, marks=quotes()))
    assert not result["accepted"] and result["reason"] == "operational_" + mode.lower()
    assert manager.funded is parent
    assert parent.snapshot() == before and parent.export_log() == history
    assert parent._last_decision_ns == manager.ops["clock_ns"] == 1


@pytest.mark.parametrize("journal_type", [FundedJournal, OperationalJournal, FixJournal])
@pytest.mark.parametrize("after_snapshot", [False, True])
def test_failed_storage_write_preserves_history_and_retry_replays(tmp_path, monkeypatch,
                                                                 journal_type, after_snapshot):
    path = tmp_path/"history.sqlite"
    with journal_type.create(path, JOURNAL_INSTRUMENTS, POLICY) as journal:
        if journal_type is not FundedJournal:
            journal.control("normal", "NORMAL", now_ns=0, reason="empty")
        before, rows = journal.recover(), list(journal._connection.iterdump())
        original = journal._write_snapshot
        def fail(snapshot):
            if after_snapshot:
                original(snapshot)
            raise RuntimeError("injected write failure")
        monkeypatch.setattr(journal, "_write_snapshot", fail)
        with pytest.raises(RuntimeError, match="injected"):
            journal.submit("retry", ORDER, now_ns=0, marks=MARKS)
        assert journal.recover() == before
        assert list(journal._connection.iterdump()) == rows
        monkeypatch.setattr(journal, "_write_snapshot", original)
        assert journal.submit("retry", ORDER, now_ns=0, marks=MARKS)["result"]["accepted"]
        final = journal.recover()
    with journal_type.open(path) as journal:
        assert journal.recover() == final
        assert journal.submit("retry", ORDER, now_ns=0, marks=MARKS)["status"] == "duplicate"
