"""Audit reproductions and bounded model checks; no new test dependency."""
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_DOWN

import pytest

from market_microstructure.fix_adapter import FixJournal, new_order, average
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession
from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER


def start(path):
    session = TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal)
    session.control('normal','NORMAL',now_ns=0,reason='empty')
    venue = SyntheticFixVenue(INSTRUMENTS)
    session.receive('ack',venue.new(new_order(session.submit('submit',ORDER,now_ns=0,marks=MARKS)['outgoing'])))
    return session, venue


def test_reserved_status_execid_is_rejected_before_it_can_alias_a_query(tmp_path):
    session, venue = start(tmp_path/'zero.sqlite')
    with session:
        trade = venue.execute('buy-1','4','99'); trade['17']='0'
        before = session.recover()
        with pytest.raises(ValueError, match='reserved'):
            session.receive('invalid-zero',trade)
        assert session.recover() == before


def test_invalid_trade_cannot_poison_persistent_missing_prefix_buffer(tmp_path):
    session, venue = start(tmp_path/'poison.sqlite')
    with session:
        venue.execute('buy-1','4','99')
        missing_prefix = venue.execute('buy-1','3','100')
        missing_prefix['31']='101'  # Outside limit, even before we know prefix.
        before = session.recover()
        with pytest.raises(ValueError,match='limit'):
            session.receive('poison',missing_prefix)
        assert session.recover() == before


def test_maximum_execid_and_query_namespace_do_not_collide(tmp_path):
    session, venue = start(tmp_path/'namespace.sqlite')
    with session:
        trade = venue.execute('buy-1','4','99'); trade['17']='x'*100
        session.receive('long-id',trade)
        # Old prefix naming mapped this Trade to the same domain event as query "proof".
        second=venue.execute('buy-1','3','100'); second['17']='query-proof'
        session.receive('second',second)
        assert session.reconcile_fix('proof',venue.query('buy-1'),now_ns=0,deadline_ns=5)['result']['matched']
        assert session.recover()['snapshot']['account']['cash'] == '304'


def test_average_rounding_does_not_depend_on_callers_decimal_context():
    fills=[dict(quantity='1',price='1'),dict(quantity='2',price='2')]
    with localcontext() as context:
        context.rounding=ROUND_DOWN
        assert average(fills) == '1.66666667'


def test_incident_recurrence_is_a_new_episode_and_blocks_normal(tmp_path):
    session, venue = start(tmp_path/'recurrence.sqlite')
    with session:
        session.reconcile_at('absent-1','buy-1',None,now_ns=5,deadline_ns=5)
        session.reconcile_fix('good',venue.query('buy-1'),now_ns=5,deadline_ns=5)
        first = session.recover()['snapshot']['operations']['incidents']
        assert len(first) == 1 and next(iter(first.values()))['status'] == 'RESOLVED'
        session.reconcile_at('absent-2','buy-1',None,now_ns=6,deadline_ns=5)
        incidents=session.recover()['snapshot']['operations']['incidents']
        assert len(incidents) == 2
        assert incidents['incident-1']['status'] == 'RESOLVED'
        assert incidents['incident-2']['status'] == 'OPEN'
        with pytest.raises(ValueError):
            session.control('unsafe','NORMAL',now_ns=6,reason='new-episode-not-resolved')


def test_bounded_state_model_preserves_cash_and_quantity_at_every_delivery(tmp_path):
    import random
    from market_microstructure.funded_journal import FundedJournal
    from market_microstructure.simulated_venue import SimulatedVenue
    # Seeds are fixed; independent arithmetic uses venue facts, never OMS funding/replay helpers.
    for seed in range(12):
        path=tmp_path/f'model-{seed}.sqlite'
        venue=SimulatedVenue(INSTRUMENTS)
        store=FundedJournal.create(path,INSTRUMENTS,POLICY)
        try:
            ack=venue.submit(store.submit('submit',ORDER,now_ns=0,marks=MARKS)['outgoing'])
            first=venue.execute('buy-1','2','98')
            store.apply('first',first)
            store.request_cancel('cancel','buy-1')
            reports=[ack,first,venue.execute('buy-1','3','99'),venue.execute('buy-1','2','100'),venue.cancel('buy-1')]
            schedule=reports*2
            random.Random(seed).shuffle(schedule)
            latest=first
            for index,report in enumerate(schedule):
                store.apply(f'delivery-{index}',report)
                if report['state']['revision'] > latest['state']['revision']:
                    latest=report
                facts={f['execution_id']:f for f in latest['state']['fills']}
                filled=sum(Decimal(f['quantity']) for f in facts.values())
                cost=sum(Decimal(f['quantity'])*Decimal(f['price']) for f in facts.values())
                snapshot=store.recover()['snapshot']; account=snapshot['account']
                remaining=Decimal(10)-filled
                canceled=remaining if latest['state']['status']=='canceled' else Decimal(0)
                working=Decimal(0) if canceled else remaining
                assert filled+working+canceled == 10
                assert Decimal(account['cash']) == 1000-cost
                assert Decimal(account['inventory']['A']) == filled
                assert Decimal(account['reserved_cash']) == working*100
                assert Decimal(account['cash']) == Decimal(account['available_cash'])+Decimal(account['reserved_cash'])
                assert snapshot['lifecycle']['executions'] == len(facts)
                if index==seed%len(schedule):
                    before=store.recover(); store.close(); store=FundedJournal.open(path)
                    assert store.recover()==before
        finally:
            store.close()
