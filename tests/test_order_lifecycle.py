"""Synthetic protocol safety, race delivery, hand accounting and replay checks."""

from copy import deepcopy
from decimal import localcontext
from itertools import permutations

import pytest

from market_microstructure.execution_contracts import KIND
from market_microstructure.order_lifecycle import OrderManager
from market_microstructure.simulated_venue import SimulatedVenue


INSTRUMENTS = {"SYNTH-BTC": {"price_tick": "1", "quantity_step": "1"}}


def order(client_id="buy-1", **changes):
    return dict(source_kind=KIND, client_order_id=client_id, symbol="SYNTH-BTC",
                side="buy", quantity="10", limit_price="100", **changes)


def setup_order():
    manager, venue = OrderManager(INSTRUMENTS), SimulatedVenue(INSTRUMENTS)
    ack = venue.submit(manager.submit(order()))
    return manager, venue, ack


def record(manager):
    return manager.snapshot()["orders"]["buy-1"]


def assert_rejected_atomically(manager, report):
    before, log = manager.snapshot(), manager.export_log()
    with pytest.raises(ValueError):
        manager.apply(report)
    assert manager.snapshot() == before
    assert manager.export_log() == log


def test_cancel_pending_accepts_another_fill_then_cancels_only_unfilled_quantity():
    manager, venue, ack = setup_order()
    assert record(manager)["status"] == "pending_new"
    assert record(manager)["last_confirmed_working_quantity"] is None
    manager.apply(ack)
    manager.apply(venue.execute("buy-1", "3", "99"))
    manager.request_cancel("buy-1")
    manager.apply(venue.execute("buy-1", "2", "100"))
    assert record(manager)["status"] == "partially_filled"
    assert record(manager)["cancel_status"] == "pending"
    manager.apply(venue.cancel("buy-1"))
    result = record(manager)
    assert (result["status"], result["cancel_status"]) == ("canceled", "canceled")
    assert result["filled_quantity"] == result["remaining_quantity"] == "5"
    assert result["last_confirmed_working_quantity"] == "0"
    assert manager.snapshot()["net_filled_quantity"] == {"SYNTH-BTC": "5"}
    assert manager.snapshot()["cash_flow"] == "-497"
    with pytest.raises(ValueError, match="terminal"):
        venue.execute("buy-1", "1", "100")


@pytest.mark.parametrize("delivery", list(permutations(range(4))))
def test_every_ack_trade_cancel_delivery_order_converges_without_double_fills(delivery):
    manager, venue, ack = setup_order()
    # Register the local cancel after observing an ACK; the original ACK may be redelivered.
    manager.apply(ack)
    manager.request_cancel("buy-1")
    reports = [ack, venue.execute("buy-1", "3", "99"),
               venue.execute("buy-1", "2", "100"), venue.cancel("buy-1")]
    for index in delivery:
        manager.apply(reports[index])
        assert manager.apply(reports[index]) == "duplicate"
    assert record(manager)["status"] == "canceled"
    assert record(manager)["cancel_status"] == "canceled"
    assert manager.snapshot()["executions"] == 2
    assert manager.snapshot()["cash_flow"] == "-497"
    assert OrderManager.replay(INSTRUMENTS, manager.export_log()).snapshot() == manager.snapshot()


def test_full_fill_wins_cancel_even_when_cancel_rejection_arrives_first():
    manager, venue, ack = setup_order()
    manager.apply(ack)
    manager.request_cancel("buy-1")
    delayed_fill = venue.execute("buy-1", "10", "100")
    manager.apply(venue.cancel("buy-1"))
    assert (record(manager)["status"], record(manager)["cancel_status"]) == ("filled", "too_late")
    manager.apply(delayed_fill)
    assert record(manager)["filled_quantity"] == "10"
    assert manager.snapshot()["executions"] == 1
    with pytest.raises(ValueError):
        manager.request_cancel("buy-1")


def test_lost_ack_query_not_found_does_not_authorize_resubmit_and_later_query_recovers():
    manager, venue = OrderManager(INSTRUMENTS), SimulatedVenue(INSTRUMENTS)
    outgoing = manager.submit(order())
    manager.mark_unknown("buy-1")
    assert record(manager)["needs_reconciliation"]
    assert venue.query("buy-1") is None  # Submission can still be in transit.
    with pytest.raises(ValueError, match="already registered"):
        manager.submit(order())
    delayed_ack = venue.submit(outgoing)
    venue.execute("buy-1", "3", "99")  # Lose both deliveries.
    manager.apply(venue.query("buy-1"))
    assert not record(manager)["needs_reconciliation"]
    assert record(manager)["filled_quantity"] == "3"
    assert manager.apply(delayed_ack) == "stale"
    same_order = venue.submit(outgoing)
    assert same_order["state"]["exchange_order_id"] == delayed_ack["state"]["exchange_order_id"]
    manager.apply(same_order)
    assert manager.snapshot()["executions"] == 1
    changed = dict(outgoing, quantity="11")
    with pytest.raises(ValueError, match="Conflicting client"):
        venue.submit(changed)


def test_cancel_timeout_live_snapshot_keeps_cancel_uncertainty_until_terminal():
    manager, venue, ack = setup_order()
    manager.apply(ack)
    manager.request_cancel("buy-1")
    manager.mark_unknown("buy-1")
    manager.apply(venue.query("buy-1"))
    assert record(manager)["cancel_status"] == "unknown"
    assert record(manager)["needs_reconciliation"]
    with pytest.raises(ValueError):
        manager.request_cancel("buy-1")
    manager.apply(venue.cancel("buy-1"))
    assert not record(manager)["needs_reconciliation"]
    assert record(manager)["cancel_status"] == "canceled"


@pytest.mark.parametrize("quantity,price", [("11", "100"), ("1", "101"), ("0", "100"),
                                           ("0.5", "100"), ("1", "99.5"), (True, "100")])
def test_invalid_venue_fill_preserves_state_and_execution_identity(quantity, price):
    manager, venue, ack = setup_order()
    before = ack["state"]
    with pytest.raises(ValueError):
        venue.execute("buy-1", quantity, price)
    assert venue.query("buy-1")["state"] == before
    report = venue.execute("buy-1", "1", "100")
    assert report["state"]["fills"][0]["execution_id"] == "execution-1"


@pytest.mark.parametrize("mutation", ["event_conflict", "execution_conflict", "same_revision",
                                     "drop_fill", "stale_new_fill", "binding", "exchange_binding",
                                     "terminal_regression", "overfill", "wrong_venue", "real_source",
                                     "bad_status_type", "bad_report_type", "bad_symbol_type"])
def test_corrupt_or_conflicting_reports_have_no_partial_effects(mutation):
    manager, venue, ack = setup_order()
    manager.apply(ack)
    first = venue.execute("buy-1", "3", "99")
    manager.apply(first)
    report = venue.execute("buy-1", "2", "100")
    if mutation == "event_conflict":
        report["event_id"] = first["event_id"]
    elif mutation == "execution_conflict":
        report["state"]["fills"][0]["price"] = "98"
    elif mutation == "same_revision":
        report["state"]["revision"] = first["state"]["revision"]
    elif mutation == "drop_fill":
        report["state"]["fills"] = report["state"]["fills"][1:]
    elif mutation == "stale_new_fill":
        report["state"]["revision"] = 1
    elif mutation == "binding":
        report["state"]["order"]["quantity"] = "11"
    elif mutation == "exchange_binding":
        report["state"]["exchange_order_id"] = "changed"
    elif mutation == "terminal_regression":
        manager.apply(venue.cancel("buy-1"))
        report["state"]["revision"] = 10
    elif mutation == "overfill":
        report["state"]["fills"][-1]["quantity"] = "8"
    elif mutation == "wrong_venue":
        report["state"]["venue_id"] = "other-venue"
    elif mutation == "real_source":
        report["source_kind"] = "REAL_OBSERVED"
    elif mutation == "bad_status_type":
        report["state"]["status"] = []
    elif mutation == "bad_report_type":
        report["report_type"] = []
    elif mutation == "bad_symbol_type":
        report["state"]["order"]["symbol"] = []
    assert_rejected_atomically(manager, report)


@pytest.mark.parametrize("collision", ["execution", "exchange_order"])
def test_global_identity_cannot_be_reused_for_another_order(collision):
    manager, venue, ack = setup_order()
    manager.apply(ack)
    first = venue.execute("buy-1", "3", "99")
    manager.apply(first)
    manager.apply(venue.submit(manager.submit(order("buy-2"))))
    second = venue.execute("buy-2", "3", "99")
    if collision == "execution":
        second["state"]["fills"][0]["execution_id"] = first["state"]["fills"][0]["execution_id"]
    else:
        second["state"]["exchange_order_id"] = first["state"]["exchange_order_id"]
    assert_rejected_atomically(manager, second)


def test_buy_sell_cash_flow_hand_answer_and_decimal_context_independence():
    with localcontext() as context:
        context.prec = 2
        manager, venue, ack = setup_order()
        manager.apply(venue.execute("buy-1", "3", "99"))
        manager.apply(venue.submit(manager.submit(dict(order("sell-1"), side="sell", quantity="2"))))
        manager.apply(venue.execute("sell-1", "2", "101"))
        assert manager.snapshot()["net_filled_quantity"] == {"SYNTH-BTC": "1"}
        assert manager.snapshot()["cash_flow"] == "-95"  # -3*99 + 2*101, not PnL.


def test_rejected_new_order_is_terminal_and_has_no_cash_or_fills():
    manager, venue = OrderManager(INSTRUMENTS), SimulatedVenue(INSTRUMENTS)
    rejection = venue.submit(manager.submit(order()), rejection_reason="synthetic_admission_rejection")
    manager.mark_unknown("buy-1")
    manager.apply(rejection)
    assert record(manager)["status"] == "rejected"
    assert record(manager)["last_confirmed_working_quantity"] == "0"
    assert manager.snapshot()["cash_flow"] == "0"
    with pytest.raises(ValueError):
        venue.execute("buy-1", "1", "99")


def test_inputs_outputs_and_exported_log_do_not_alias_internal_state():
    registry = deepcopy(INSTRUMENTS)
    manager, venue = OrderManager(registry), SimulatedVenue(registry)
    outgoing = manager.submit(order())
    ack = venue.submit(outgoing)
    outgoing["quantity"] = "999"
    manager.apply(ack)
    ack["state"]["order"]["quantity"] = "999"
    registry.clear()
    exported = manager.export_log()
    snapshot = manager.snapshot()
    exported[0]["order"]["quantity"] = "999"
    snapshot["orders"].clear()
    assert record(manager)["order"]["quantity"] == "10"
    assert venue.query("buy-1")["state"]["order"]["quantity"] == "10"
    log = manager.export_log()
    for prefix in range(len(log) + 1):
        recovered = OrderManager.replay(INSTRUMENTS, log[:prefix])
        for entry in log[prefix:]:
            if entry["action"] == "apply":
                recovered.apply(entry["report"])
            elif entry["action"] == "submit":
                recovered.submit(entry["order"])
        assert recovered.snapshot() == manager.snapshot()


@pytest.mark.parametrize("bad_log", [None, [{"action": "network_send"}], [None],
                                    [{"action": "submit", "order": order(), "extra": True}]])
def test_log_replay_rejects_unknown_operations(bad_log):
    with pytest.raises(ValueError):
        OrderManager.replay(INSTRUMENTS, bad_log)


@pytest.mark.parametrize("change", [dict(source_kind="REAL_OBSERVED"), dict(symbol="UNKNOWN"),
                                   dict(symbol=[]), dict(quantity="0"), dict(quantity="NaN"),
                                   dict(quantity="0.5"), dict(limit_price="100.1"),
                                   dict(side="short"), dict(client_order_id="")])
def test_invalid_submission_is_rejected_before_registration(change):
    manager, venue = OrderManager(INSTRUMENTS), SimulatedVenue(INSTRUMENTS)
    invalid = dict(order(), **change)
    with pytest.raises(ValueError):
        manager.submit(invalid)
    with pytest.raises(ValueError):
        venue.submit(invalid)
    assert manager.snapshot()["orders"] == {} and manager.export_log() == []
    assert venue.query("buy-1") is None


@pytest.mark.parametrize("revision", [0, -1, True, "1", 2**63])
def test_revision_requires_bounded_positive_integer(revision):
    manager, venue, ack = setup_order()
    ack["state"]["revision"] = revision
    assert_rejected_atomically(manager, ack)


def test_stale_terminal_report_cannot_precede_a_different_terminal_outcome():
    manager, venue, ack = setup_order()
    manager.apply(venue.execute("buy-1", "10", "100"))
    stale = deepcopy(ack)
    stale["report_type"] = "canceled"
    stale["state"]["status"] = "canceled"
    assert_rejected_atomically(manager, stale)


def test_ack_cannot_precede_new_order_rejection_even_when_delivered_late():
    manager, venue = OrderManager(INSTRUMENTS), SimulatedVenue(INSTRUMENTS)
    reject = venue.submit(manager.submit(order()), rejection_reason="artificial_rejection")
    manager.apply(reject)
    stale = deepcopy(reject)
    stale["event_id"] = "corrupt-old-ack"
    stale["report_type"] = "accepted"
    stale["state"].update(revision=1, status="open", rejection_reason=None)
    assert_rejected_atomically(manager, stale)


def test_event_bound_failure_does_not_change_venue_order_or_manager(monkeypatch):
    from market_microstructure import order_protocol
    manager, venue, ack = setup_order()
    manager.apply(ack)
    before, log = manager.snapshot(), manager.export_log()
    monkeypatch.setattr(order_protocol, "MAX_EVENTS", 2)
    with pytest.raises(ValueError, match="bound"):
        manager.request_cancel("buy-1")
    assert manager.snapshot() == before and manager.export_log() == log
    venue.query("buy-1")  # Venue's second report exhausts its separate bound.
    with pytest.raises(ValueError, match="bound"):
        venue.cancel("buy-1")
    monkeypatch.setattr(order_protocol, "MAX_EVENTS", 10000)
    assert venue.query("buy-1")["state"] == ack["state"]
