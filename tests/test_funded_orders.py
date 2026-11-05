from copy import deepcopy
from decimal import localcontext
from itertools import permutations
import json

import pytest

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_orders import FundedOrderManager
from market_microstructure.simulated_venue import SimulatedVenue


INSTRUMENTS = {s: dict(price_tick="1", quantity_step="1") for s in ("A", "B")}


def policy(cash="1000", order_cap="10000", symbol_cap="10000", total_cap="20000"):
    return dict(source_kind=KIND, initial_cash=cash, session_id="session-1", max_quote_age_ns=10,
                risk_limits=dict(max_order_notional=order_cap, max_symbol_gross={s: symbol_cap for s in INSTRUMENTS},
                                 max_portfolio_gross=total_cap))


def quotes(price="100", **changes):
    return {s: dict(dict(quote_id=f"{s}-q", timestamp_ns=0, available_ns=0, session_id="session-1",
                         valid=True, price=price), **changes) for s in INSTRUMENTS}


def order(client_id="buy-1", **changes):
    return dict(dict(source_kind=KIND, client_order_id=client_id, symbol="A", side="buy",
                     quantity="10", limit_price="100"), **changes)


def setup_funded(config=None):
    return FundedOrderManager(INSTRUMENTS, config or policy()), SimulatedVenue(INSTRUMENTS)


def submit(manager, spec=None, now=0, marks=None):
    return manager.submit(spec or order(), now_ns=now, marks=quotes() if marks is None else marks)


def filled_inventory(quantity="5"):
    manager, venue = setup_funded()
    result = submit(manager, order(quantity=quantity))
    venue.submit(result["order"])
    manager.apply(venue.execute("buy-1", quantity, "99"))
    return manager, venue


def test_pending_and_unknown_orders_reserve_cash_before_delivery_or_ack():
    manager, venue = setup_funded()
    result = submit(manager)
    assert result["accepted"] and result["order"] == order()
    assert manager.snapshot()["account"]["available_cash"] == "0"
    manager.mark_unknown("buy-1")
    assert venue.query("buy-1") is None
    rejected = submit(manager, order("buy-2", symbol="B", quantity="1"))
    assert not rejected["accepted"] and rejected["order"] is None
    assert rejected["reason"] == "insufficient_cash"
    assert "buy-2" not in manager.snapshot()["lifecycle"]["orders"]
    assert manager.snapshot()["account"]["reserved_cash"] == "1000"


def test_partial_fill_price_improvement_and_cancel_timeout_release_only_after_confirmation():
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    manager.apply(venue.execute("buy-1", "3", "99"))
    balances = manager.snapshot()["account"]
    assert (balances["cash"], balances["reserved_cash"], balances["available_cash"]) == ("703", "700", "3")
    manager.request_cancel("buy-1")
    manager.mark_unknown("buy-1")
    manager.apply(venue.query("buy-1"))
    assert manager.snapshot()["account"] == balances
    assert submit(manager, order("buy-2", quantity="1"))["reason"] == "insufficient_cash"
    manager.apply(venue.cancel("buy-1"))
    assert manager.snapshot()["account"]["available_cash"] == "703"
    # A local admission denial created no external order and may be retried.
    assert submit(manager, order("buy-2", quantity="1"))["accepted"]


@pytest.mark.parametrize("delivery", list(permutations(range(3))))
def test_cancel_cumulative_report_and_late_trades_preserve_cash_and_reservations(delivery):
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    manager.request_cancel("buy-1")
    reports = [venue.execute("buy-1", "3", "99"), venue.execute("buy-1", "2", "100"), venue.cancel("buy-1")]
    for index in delivery:
        manager.apply(reports[index])
        before = manager.snapshot()
        assert manager.apply(reports[index]) == "duplicate"
        assert manager.snapshot() == before
    balances = manager.snapshot()["account"]
    assert (balances["cash"], balances["reserved_cash"], balances["available_cash"]) == ("503", "0", "503")
    assert balances["inventory"] == {"A": "5", "B": "0"}
    assert FundedOrderManager.replay(json.loads(json.dumps(manager.export_log()))).snapshot() == manager.snapshot()


def test_new_order_rejection_releases_all_reserved_cash():
    manager, venue = setup_funded()
    rejected = venue.submit(submit(manager)["order"], rejection_reason="synthetic_rejection")
    manager.mark_unknown("buy-1")
    manager.apply(rejected)
    assert manager.snapshot()["account"]["reserved_cash"] == "0"
    assert manager.snapshot()["account"]["available_cash"] == "1000"


def test_pending_sell_locks_inventory_without_crediting_prospective_proceeds():
    manager, venue = filled_inventory()
    sale = submit(manager, order("sell-1", side="sell", quantity="4", limit_price="110"), marks={})
    assert sale["accepted"]  # Only an unlocked holding can be sold.
    manager.mark_unknown("sell-1")
    balances = manager.snapshot()["account"]
    assert balances["available_inventory"] == {"A": "1", "B": "0"}
    assert balances["available_cash"] == "505"
    assert submit(manager, order("sell-2", side="sell", quantity="2"))["reason"] == "insufficient_inventory"
    assert submit(manager, order("buy-2", quantity="6"))["reason"] == "insufficient_cash"
    venue.submit(sale["order"])
    manager.apply(venue.execute("sell-1", "2", "111"))
    balances = manager.snapshot()["account"]
    assert (balances["cash"], balances["inventory"]["A"], balances["reserved_inventory"]["A"]) == ("727", "3", "2")
    assert balances["available_inventory"]["A"] == "1"
    manager.request_cancel("sell-1")
    assert manager.snapshot()["account"] == balances
    manager.apply(venue.cancel("sell-1"))
    assert manager.snapshot()["account"]["available_inventory"]["A"] == "3"


def test_unconfirmed_buy_fills_cannot_back_a_sale_and_short_sales_are_rejected():
    manager, venue = setup_funded()
    assert submit(manager, order("short", side="sell", quantity="1"))["reason"] == "insufficient_inventory"
    venue.submit(submit(manager)["order"])
    fill = venue.execute("buy-1", "2", "99")
    assert submit(manager, order("sell", side="sell", quantity="1"))["reason"] == "insufficient_inventory"
    manager.apply(fill)
    assert submit(manager, order("sell", side="sell", quantity="1"))["accepted"]


@pytest.mark.parametrize("config,expected", [(policy(order_cap="999"), "max_order_notional_exceeded"),
                                            (policy(symbol_cap="999"), "max_symbol_gross_exceeded"),
                                            (policy(total_cap="999"), "max_portfolio_gross_exceeded")])
def test_admission_uses_order_symbol_and_portfolio_limits(config, expected):
    manager, venue = setup_funded(config)
    before = manager.snapshot()
    decision = submit(manager)
    assert not decision["accepted"] and decision["reason"] == expected
    assert manager.snapshot() == before
    assert venue.query("buy-1") is None
    assert len(manager.export_log()["events"]) == 1  # Denial is reproducible, not a live order.


def test_exact_cap_and_cash_boundaries_are_admitted():
    manager, venue = setup_funded(policy(order_cap="1000", symbol_cap="1000", total_cap="1000"))
    assert submit(manager)["accepted"]
    assert manager.snapshot()["account"]["available_cash"] == "0"


def test_pending_orders_share_symbol_and_portfolio_limits_without_sell_netting():
    manager, venue = setup_funded(policy(cash="5000", total_cap="1000"))
    assert submit(manager, order(quantity="6"))["accepted"]
    denied = submit(manager, order("buy-b", symbol="B", quantity="5"))
    assert denied["reason"] == "max_portfolio_gross_exceeded"
    assert denied["projected"]["committed_gross"] == "1100"
    assert manager.assess(now_ns=0, marks=quotes())["committed_gross"] == "600"


def test_mark_above_limit_is_conservative_and_below_limit_does_not_free_buy_commitment():
    manager, venue = setup_funded(policy(symbol_cap="1000"))
    denied = submit(manager, marks=quotes(price="101"))
    assert denied["reason"] == "max_symbol_gross_exceeded"
    assert denied["projected"]["committed_gross"] == "1010"
    assert submit(manager, marks=quotes(price="90"))["accepted"]
    assert manager.assess(now_ns=0, marks=quotes(price="90"))["committed_gross"] == "1000"


@pytest.mark.parametrize("marks,now,reason", [({}, 0, "missing_quote"),
    (quotes(valid=False), 0, "invalid_quote"), (quotes(session_id="other"), 0, "session_changed"),
    (quotes(available_ns=1), 0, "quote_not_yet_available"), (quotes(), 11, "stale_quote")])
def test_missing_invalid_future_stale_and_wrong_session_marks_block_buys(marks, now, reason):
    manager, venue = setup_funded()
    decision = submit(manager, marks=marks, now=now)
    assert decision["reason"] == "portfolio_mark_unavailable"
    assert decision["projected"]["marks"]["A"]["reason"] == reason
    assert decision["projected"]["committed_gross"] is None
    assert decision["projected"]["limit_checks"][-1]["passed"] is None
    assert manager.snapshot()["account"]["reserved_cash"] == "0"


def test_quote_age_exact_boundary_and_available_at_now_are_valid():
    manager, venue = setup_funded()
    assert submit(manager, now=10, marks=quotes(available_ns=10))["accepted"]


def test_current_breach_and_missing_marks_do_not_block_reports_or_inventory_reduction():
    manager, venue = setup_funded(policy(symbol_cap="500", total_cap="500", order_cap="500"))
    venue.submit(submit(manager, order(quantity="5"))["order"])
    manager.apply(venue.execute("buy-1", "5", "100"))
    observation = manager.assess(now_ns=0, marks=quotes(price="200"))
    assert observation["committed_gross"] == "1000"
    assert observation["limit_checks"][0]["passed"] is False
    sale = submit(manager, order("sell-1", side="sell", quantity="5", limit_price="1000"), marks={})
    assert sale["accepted"]  # Order cap is an entry cap, not a reduction cap.
    venue.submit(sale["order"])
    manager.apply(venue.execute("sell-1", "5", "1001"))
    assert manager.snapshot()["account"]["cash"] == "5505"
    assert manager.snapshot()["account"]["inventory"]["A"] == "0"


def test_pending_sell_does_not_reduce_buy_exposure_budget():
    manager, venue = setup_funded(policy(cash="2000", symbol_cap="500"))
    venue.submit(submit(manager, order(quantity="5"))["order"])
    manager.apply(venue.execute("buy-1", "5", "100"))
    assert submit(manager, order("sell-1", side="sell", quantity="5"))["accepted"]
    decision = submit(manager, order("buy-2", quantity="1"))
    assert decision["reason"] == "max_symbol_gross_exceeded"
    assert decision["projected"]["symbol_committed_gross"]["A"] == "600"


def test_corrupt_reports_preserve_both_reservations_and_lifecycle_log():
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    fill = venue.execute("buy-1", "1", "100")
    fill["state"]["fills"][0]["price"] = "101"
    snapshot, log = manager.snapshot(), manager.export_log()
    with pytest.raises(ValueError):
        manager.apply(fill)
    assert manager.snapshot() == snapshot and manager.export_log() == log


def test_low_decimal_precision_does_not_round_reservations_or_cash():
    with localcontext() as context:
        context.prec = 2
        manager, venue = setup_funded(policy(cash="1001"))
        venue.submit(submit(manager)["order"])
        manager.apply(venue.execute("buy-1", "3", "99"))
        balances = manager.snapshot()["account"]
        assert (balances["cash"], balances["reserved_cash"], balances["available_cash"]) == ("704", "700", "4")


@pytest.mark.parametrize("field,value", [("accepted", False), ("reason", "tampered")])
def test_replay_detects_changed_admission_outcome(field, value):
    manager, venue = setup_funded()
    submit(manager)
    log = manager.export_log()
    log["events"][0][field] = value
    with pytest.raises(ValueError, match="outcome changed"):
        FundedOrderManager.replay(log)


def test_caller_mutation_does_not_change_policy_marks_log_or_balances():
    config, instruments, marks = policy(), deepcopy(INSTRUMENTS), quotes()
    manager = FundedOrderManager(instruments, config)
    decision = submit(manager, marks=marks)
    before = manager.snapshot()
    config["initial_cash"] = "1"
    instruments.clear()
    marks.clear()
    decision["order"]["quantity"] = "1"
    log = manager.export_log()
    log["policy"]["initial_cash"] = "1"
    assert manager.snapshot() == before
    assert FundedOrderManager.replay(manager.export_log()).snapshot() == before


@pytest.mark.parametrize("invalid", [None, {"action": "send"}, {"source_kind": KIND}])
def test_invalid_log_envelope_is_rejected(invalid):
    with pytest.raises(ValueError):
        FundedOrderManager.replay(invalid)


def test_backdating_decision_cannot_bypass_stale_mark_rejection():
    manager, venue = setup_funded()
    assert submit(manager, now=11)["reason"] == "portfolio_mark_unavailable"
    log = manager.export_log()
    with pytest.raises(ValueError, match="backwards"):
        submit(manager, now=0)
    with pytest.raises(ValueError, match="backwards"):
        manager.assess(now_ns=0, marks=quotes())
    assert manager.export_log() == log
    assert submit(manager, now=11, marks=quotes(timestamp_ns=11, available_ns=11))["accepted"]
    assert FundedOrderManager.replay(manager.export_log()).snapshot() == manager.snapshot()


@pytest.mark.parametrize("change", [{"price": "NaN"}, {"price": 100}, {"valid": 1},
                                   {"timestamp_ns": True}, {"timestamp_ns": 2, "available_ns": 1},
                                   {"session_id": ""}, {"extra": 1}])
def test_malformed_marks_cause_no_order_reservation_or_log_mutation(change):
    manager, venue = setup_funded()
    before, log = manager.snapshot(), manager.export_log()
    with pytest.raises(ValueError):
        submit(manager, marks=quotes(**change))
    assert manager.snapshot() == before and manager.export_log() == log


@pytest.mark.parametrize("field,value", [("source_kind", "REAL_OBSERVED"), ("initial_cash", "0"),
                                       ("max_quote_age_ns", 0), ("max_quote_age_ns", True),
                                       ("session_id", ""), ("risk_limits", None)])
def test_invalid_policy_is_rejected(field, value):
    with pytest.raises(ValueError):
        setup_funded(dict(policy(), **{field: value}))


def test_one_unavailable_holding_preserves_known_symbol_breach_but_blocks_new_buys():
    manager, venue = setup_funded(policy(cash="5000", symbol_cap="500"))
    for symbol in INSTRUMENTS:
        client_id = f"buy-{symbol}"
        venue.submit(submit(manager, order(client_id, symbol=symbol, quantity="5"))["order"])
        manager.apply(venue.execute(client_id, "5", "100"))
    observation = manager.assess(now_ns=0, marks={"A": quotes(price="101")["A"]})
    assert observation["committed_gross"] is None and observation["equity"] is None
    assert observation["symbol_committed_gross"] == {"A": "505", "B": None}
    checks = observation["limit_checks"]
    assert checks[0]["passed"] is False and checks[1]["passed"] is None
    assert checks[-1]["passed"] is None
    sale = submit(manager, order("reduce-B", symbol="B", side="sell", quantity="5"), marks={})
    assert sale["accepted"]


def test_full_fill_wins_cancel_releases_remaining_cash_without_double_spend():
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    manager.request_cancel("buy-1")
    fill = venue.execute("buy-1", "10", "99")
    manager.apply(venue.cancel("buy-1"))
    manager.apply(fill)
    balances = manager.snapshot()["account"]
    assert (balances["cash"], balances["reserved_cash"], balances["available_cash"]) == ("10", "0", "10")
    assert balances["inventory"]["A"] == "10"
    assert manager.snapshot()["lifecycle"]["orders"]["buy-1"]["cancel_status"] == "too_late"


def test_funded_log_bound_does_not_release_a_pending_reservation(monkeypatch):
    from market_microstructure import order_protocol
    manager, venue = setup_funded()
    manager.apply(venue.submit(submit(manager)["order"]))
    monkeypatch.setattr(order_protocol, "MAX_EVENTS", 2)
    snapshot, log = manager.snapshot(), manager.export_log()
    with pytest.raises(ValueError, match="bound"):
        manager.request_cancel("buy-1")
    assert manager.snapshot() == snapshot and manager.export_log() == log


@pytest.mark.parametrize("event", [None, {"action": []}, {"action": "network_send"},
                                  {"action": "apply", "report": None, "extra": True}])
def test_funded_replay_rejects_malformed_operations(event):
    manager, venue = setup_funded()
    log = manager.export_log()
    log["events"] = [event]
    with pytest.raises(ValueError):
        FundedOrderManager.replay(log)


def test_computed_remaining_amount_may_have_more_digits_than_each_input():
    from decimal import Decimal
    instruments = {"A": dict(price_tick="0.000000000001", quantity_step="0.000000000001")}
    config = policy(cash="1000000000000", order_cap="1000000000000", total_cap="1000000000000")
    config["risk_limits"]["max_symbol_gross"] = {"A": "1000000000000"}
    manager, venue = FundedOrderManager(instruments, config), SimulatedVenue(instruments)
    marks = {"A": quotes(price="0.000000000001")["A"]}
    decision = submit(manager, order(quantity="100000000000000000000000", limit_price="0.000000000001"), marks=marks)
    venue.submit(decision["order"])
    manager.apply(venue.execute("buy-1", "0.000000000001", "0.000000000001"))
    balances = manager.snapshot()["account"]
    assert Decimal(balances["available_cash"]) == Decimal("900000000000")
    assert Decimal(balances["cash"]) == Decimal("999999999999.999999999999999999999999")
    assert manager.assess(now_ns=0, marks=marks)["committed_gross"] == "100000000000.000000000000000000000000"
