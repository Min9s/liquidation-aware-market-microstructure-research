"""Quantity/lot admission must cover every supported FIX execution revision."""
from decimal import Decimal, localcontext
import json
import subprocess
import sys

import pytest

from market_microstructure.fix_adapter import FixJournal, new_order, parse_new
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession
from test_funded_journal import POLICY as BASE_POLICY, MARKS as BASE_MARKS, ORDER


INSTRUMENTS = {'A': dict(price_tick='1', quantity_step='0.000000000001')}
POLICY = dict(BASE_POLICY, initial_cash='100000000000000000000', risk_limits=dict(
    max_order_notional='100000000000000000000', max_symbol_gross={'A': '100000000000000000000'},
    max_portfolio_gross='100000000000000000000'))
MARKS = {'A': dict(BASE_MARKS['A'], price='1')}


@pytest.mark.parametrize('quantity', ['1000000000000', '922337.203685477581'])
def test_admission_cannot_defer_revision_overflow_until_a_valid_fill(tmp_path, quantity):
    """Either reject atomically at admission or book the valid full execution."""
    with TradingSession.create(tmp_path/'overflow.sqlite', INSTRUMENTS, POLICY,
                               journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        order = dict(ORDER, quantity=quantity, limit_price='1')
        before = session.recover()
        try:
            result = session.submit('submit', order, now_ns=0, marks=MARKS)
        except ValueError as error:
            assert 'FIX revision' in str(error)
            assert session.recover() == before
            return
        assert result['result']['accepted']
        venue = SyntheticFixVenue(INSTRUMENTS)
        session.receive('ack', venue.new(new_order(result['outgoing'])))
        session.receive('fill', venue.execute(order['client_order_id'], quantity, '1'))
        account = session.recover()['snapshot']['account']
        assert Decimal(account['inventory']['A']) == Decimal(quantity)
        with localcontext() as context:
            context.prec = 128
            assert Decimal(account['cash']) == Decimal(POLICY['initial_cash']) - Decimal(quantity)
        assert Decimal(account['reserved_cash']) == 0


@pytest.mark.parametrize('side', ['buy', 'sell'])
def test_unrepresentable_submission_rolls_back_the_entire_journal(tmp_path, side):
    with TradingSession.create(tmp_path/'admission.sqlite', INSTRUMENTS, POLICY,
                               journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        venue = SyntheticFixVenue(INSTRUMENTS)
        seed = session.submit('seed', dict(ORDER, quantity='2', limit_price='1'), now_ns=0, marks=MARKS)
        session.receive('seed-ack', venue.new(new_order(seed['outgoing'])))
        session.receive('seed-trade', venue.execute('buy-1', '1', '1'))
        before = session.recover()
        rows = list(session.journal._connection.iterdump())
        bad = dict(ORDER, client_order_id='overflow', side=side,
                   quantity='922337.203685477581', limit_price='1')
        with pytest.raises(ValueError, match='FIX revision'):
            session.submit('invalid', bad, now_ns=1, marks=MARKS)
        assert session.recover() == before
        assert list(session.journal._connection.iterdump()) == rows
        # Neither the command ID nor client ID nor decision clock was consumed.
        good = dict(bad, quantity='1')
        assert session.submit('invalid', good, now_ns=0, marks=MARKS)['result']['accepted']


@pytest.mark.parametrize('quantity,step', [
    ('922337.203685477581', '0.000000000001'),
    ('9.22337203685477581E5', '1E-12'),
    ('922337203685477581', '1'),
])
def test_fix_new_order_parser_rejects_revision_overflow(quantity, step):
    registry = {'A': dict(price_tick='1', quantity_step=step)}
    with localcontext() as context:
        context.prec = 6
        with pytest.raises(ValueError, match='FIX revision'):
            parse_new(new_order(dict(ORDER, quantity=quantity, limit_price='1')), registry)


@pytest.mark.parametrize('quantity,step', [
    ('922337.203685477580', '0.000000000001'),
    ('922337203685477580', '1'),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_largest_supported_order_fills_deduplicates_and_replays(tmp_path, quantity, step, reverse):
    registry = {'A': dict(price_tick='1', quantity_step=step)}
    order = dict(ORDER, quantity=quantity, limit_price='1')
    with localcontext() as context:
        context.prec = 6
        assert parse_new(new_order(order), registry) == order
    path = tmp_path/'boundary.sqlite'
    venue = SyntheticFixVenue(registry)
    with TradingSession.create(path, registry, POLICY, journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        submitted = session.submit('submit', order, now_ns=0, marks=MARKS)
        session.receive('ack', venue.new(new_order(submitted['outgoing'])))
        first = venue.execute('buy-1', step, '1')
        final = venue.execute('buy-1', str(Decimal(quantity)-Decimal(step)), '1')
        if reverse:
            assert session.receive('final', final)['result']['buffered']
            assert session.recover()['snapshot']['lifecycle']['executions'] == 0
        else:
            session.receive('first', first)
            session.receive('first-again', first)
    # Restart with an unresolved prefix or a partial fill, then complete it.
    with TradingSession.open(path, journal_type=FixJournal) as session:
        if reverse:
            assert session.recover()['snapshot']['operations']['fix']['gaps'] == ['buy-1']
            session.receive('first-again', first)
        session.receive('final-again', final)
        session.receive('stale-ack', venue.archive[0])
        session.receive('stale-first', first)
        assert session.receive('final-again', final)['status'] == 'duplicate'
        snapshot = session.recover()['snapshot']
        account = snapshot['account']
        with localcontext() as context:
            context.prec = 128
            assert Decimal(account['cash']) == Decimal(POLICY['initial_cash'])-Decimal(quantity)
        assert Decimal(account['inventory']['A']) == Decimal(quantity)
        assert Decimal(account['reserved_cash']) == 0
        assert snapshot['lifecycle']['executions'] == 2
        assert snapshot['lifecycle']['orders']['buy-1']['revision'] == 9223372036854775802
        assert session.reconcile_fix('query', venue.query('buy-1'), now_ns=0,
                                     deadline_ns=5)['result']['matched']
    with FixJournal.open(path) as journal:
        assert journal.recover()['snapshot']['account'] == account
        stored = json.loads(journal._connection.execute(
            'SELECT payload FROM snapshot WHERE id=1').fetchone()[0])
        assert stored['lifecycle']['orders']['buy-1']['revision'] == 9223372036854775802


def test_boundary_partial_fill_can_cancel_and_reconcile(tmp_path):
    quantity = '922337.203685477580'
    with TradingSession.create(tmp_path/'cancel.sqlite', INSTRUMENTS, POLICY,
                               journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        venue = SyntheticFixVenue(INSTRUMENTS)
        result = session.submit('submit', dict(ORDER, quantity=quantity, limit_price='1'),
                                now_ns=0, marks=MARKS)
        session.receive('ack', venue.new(new_order(result['outgoing'])))
        filled = str(Decimal(quantity)-Decimal('0.000000000001'))
        session.receive('partial', venue.execute('buy-1', filled, '1'))
        pending = venue.request_cancel(session.cancel_fix('cancel', 'buy-1', 'cancel-1')['outgoing'])
        session.receive('pending', pending)
        session.receive('canceled', venue.confirm_cancel('buy-1'))
        assert session.reconcile_fix('query', venue.query('buy-1'), now_ns=0,
                                     deadline_ns=5)['result']['matched']
        snapshot = session.recover()['snapshot']
        assert snapshot['lifecycle']['orders']['buy-1']['revision'] == 9223372036854775794
        assert Decimal(snapshot['account']['reserved_cash']) == 0
        assert Decimal(snapshot['account']['inventory']['A']) == Decimal(filled)


def test_quantity_bound_is_specific_to_the_fix_adapter(tmp_path):
    """Native cumulative reports use supplied revisions, not quantity-derived ones."""
    with FundedJournal.create(tmp_path/'native.sqlite', INSTRUMENTS, POLICY) as journal:
        order = dict(ORDER, quantity='1000000000000', limit_price='1')
        result = journal.submit('submit', order, now_ns=0, marks=MARKS)
        assert result['result']['accepted']
        venue = SimulatedVenue(INSTRUMENTS)
        journal.apply('ack', venue.submit(result['outgoing']))
        journal.apply('fill', venue.execute('buy-1', order['quantity'], '1'))
        assert journal.recover()['snapshot']['lifecycle']['orders']['buy-1']['revision'] == 2


@pytest.mark.parametrize('lots,accepted', [
    (922337203685477579, True),
    (922337203685477580, True),
    (922337203685477581, False),
])
def test_admission_boundary_is_exact_and_context_independent(lots, accepted):
    with localcontext() as context:
        context.prec = 128
        quantity = str(Decimal(lots) * Decimal('0.000000000001'))
    order = dict(ORDER, quantity=quantity, limit_price='1')
    with localcontext() as context:
        context.prec = 6
        if accepted:
            assert parse_new(new_order(order), INSTRUMENTS) == order
        else:
            with pytest.raises(ValueError, match='FIX revision'):
                parse_new(new_order(order), INSTRUMENTS)


@pytest.mark.parametrize('terminal', ['filled', 'canceled'])
def test_boundary_cancel_fill_race_preserves_reservations_and_replays(tmp_path, terminal):
    path = tmp_path/'race.sqlite'
    quantity = '922337.203685477580'
    first_quantity = '922337.203685477578'
    venue = SyntheticFixVenue(INSTRUMENTS)
    with TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        outgoing = session.submit('submit', dict(ORDER, quantity=quantity, limit_price='1'),
                                  now_ns=0, marks=MARKS)['outgoing']
        session.receive('ack', venue.new(new_order(outgoing)))
        session.receive('first', venue.execute('buy-1', first_quantity, '1'))
        pending = venue.request_cancel(session.cancel_fix('cancel', 'buy-1', 'cancel-1')['outgoing'])
        # The pending response is delayed until after another actual execution.
        last = venue.execute('buy-1', '0.000000000002' if terminal == 'filled'
                             else '0.000000000001', '1')
        session.receive('racing-fill', last)
        account = session.recover()['snapshot']['account']
        assert Decimal(account['reserved_cash']) == (0 if terminal == 'filled' else Decimal('1e-12'))
        session.receive('late-pending', pending)
        assert session.recover()['snapshot']['account'] == account
        confirmation = venue.confirm_cancel('buy-1')
        if terminal == 'filled':
            session.cancel_rejected('too-late', confirmation)
        else:
            session.receive('canceled', confirmation)
        session.receive('duplicate-fill', last)
        snapshot = session.recover()['snapshot']
        order = snapshot['lifecycle']['orders']['buy-1']
        assert order['status'] == terminal
        assert order['cancel_status'] == ('too_late' if terminal == 'filled' else 'canceled')
        assert Decimal(order['filled_quantity']) + Decimal(order['remaining_quantity']) == Decimal(quantity)
        assert snapshot['lifecycle']['executions'] == 2
        account = snapshot['account']
        assert Decimal(account['reserved_cash']) == 0
        with localcontext() as context:
            context.prec = 128
            assert Decimal(account['cash']) + Decimal(account['inventory']['A']) == Decimal(POLICY['initial_cash'])
        assert session.reconcile_fix('query', venue.query('buy-1'), now_ns=0,
                                     deadline_ns=5)['result']['matched']
    with TradingSession.open(path, journal_type=FixJournal) as session:
        session.receive('replayed-fill', last)
        assert session.recover()['snapshot']['account'] == account
        assert session.recover()['snapshot']['lifecycle']['executions'] == 2
        assert session.reconcile_fix('restart-query', venue.query('buy-1'), now_ns=0,
                                     deadline_ns=5)['result']['matched']


@pytest.mark.parametrize('tag,value', [
    ('14', '922337.203685477581'),  # One lot over the admitted order.
    ('151', '0.000000000001'),  # Leaves after a terminal fill must be zero.
    ('32', '0.0000000000001'),  # Smaller than one lot.
    ('17', 'execution-1'),  # Same ExecID, different immutable trade facts.
])
def test_boundary_rejected_reports_leave_sql_and_account_unchanged(tmp_path, tag, value):
    with TradingSession.create(tmp_path/'invalid.sqlite', INSTRUMENTS, POLICY,
                               journal_type=FixJournal) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        outgoing = session.submit('submit', dict(ORDER, quantity='922337.203685477580',
                                  limit_price='1'), now_ns=0, marks=MARKS)['outgoing']
        venue = SyntheticFixVenue(INSTRUMENTS)
        session.receive('ack', venue.new(new_order(outgoing)))
        session.receive('first', venue.execute('buy-1', '0.000000000001', '1'))
        final = venue.execute('buy-1', '922337.203685477579', '1')
        before = session.recover()
        rows = list(session.journal._connection.iterdump())
        with pytest.raises(ValueError):
            session.receive('retryable', dict(final, **{tag: value}))
        assert session.recover() == before
        assert list(session.journal._connection.iterdump()) == rows
        session.receive('retryable', final)
        assert session.recover()['snapshot']['lifecycle']['executions'] == 2


@pytest.mark.parametrize('boundary', ['before_snapshot', 'before_commit', 'after_commit'])
def test_boundary_fill_is_atomic_across_abrupt_process_exit(tmp_path, boundary):
    path = tmp_path/'crash.sqlite'
    quantity = '922337.203685477580'
    venue = SyntheticFixVenue(INSTRUMENTS)
    with FixJournal.create(path, INSTRUMENTS, POLICY) as journal:
        journal.control('normal', 'NORMAL', now_ns=0, reason='empty')
        outgoing = journal.submit('submit', dict(ORDER, quantity=quantity, limit_price='1'),
                                  now_ns=0, marks=MARKS)['outgoing']
        journal.receive('ack', venue.new(new_order(outgoing)))
        before = journal.recover()
    fill = venue.execute('buy-1', quantity, '1')
    # Exercise the journal transaction directly, without a session-start command
    # obscuring the exact pre/post-commit comparison.
    script = '''
import json, os, sys
from market_microstructure.fix_adapter import FixJournal
journal = FixJournal.open(sys.argv[1])
original = journal._write_snapshot
if sys.argv[2] == 'before_snapshot':
    journal._write_snapshot = lambda snapshot: os._exit(73)
elif sys.argv[2] == 'before_commit':
    def crash(snapshot):
        original(snapshot)
        os._exit(73)
    journal._write_snapshot = crash
else:
    class ExitAfterCommit:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, name): return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            os._exit(73)
    journal._connection = ExitAfterCommit(journal._connection)
journal.receive('fill', json.loads(sys.argv[3]))
raise AssertionError('exit hook not reached')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(path), boundary,
                             json.dumps(fill)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 73, result.stderr
    with FixJournal.open(path) as journal:
        recovered = journal.recover()
        if boundary != 'after_commit':
            assert recovered == before
        else:
            assert recovered['committed_commands'] == before['committed_commands'] + 1
        retry = journal.receive('fill', fill)
        assert retry['status'] == ('duplicate' if boundary == 'after_commit' else 'committed')
        account = journal.recover()['snapshot']['account']
        assert Decimal(account['inventory']['A']) == Decimal(quantity)
        assert Decimal(account['reserved_cash']) == 0
        with localcontext() as context:
            context.prec = 128
            assert Decimal(account['cash']) + Decimal(quantity) == Decimal(POLICY['initial_cash'])
        assert journal.recover()['snapshot']['lifecycle']['executions'] == 1
        final = journal.recover()
    with FixJournal.open(path) as journal:
        assert journal.recover() == final
