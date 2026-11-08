from copy import deepcopy
from itertools import permutations

import pytest

from market_microstructure.fix_adapter import FixJournal, new_order, parse_new
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession
from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER


def prepare(path):
    session = TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal)
    session.control('normal', 'NORMAL', now_ns=0, reason='empty')
    venue = SyntheticFixVenue(INSTRUMENTS)
    outgoing = session.submit('submit', ORDER, now_ns=0, marks=MARKS)['outgoing']
    ack = venue.new(new_order(outgoing))
    first = venue.execute('buy-1', '4', '99')
    session.receive('four', first)
    cancel = session.cancel_fix('cancel', 'buy-1', 'cancel-1')['outgoing']
    pending = venue.request_cancel(cancel)
    second = venue.execute('buy-1', '3', '100')
    terminal = venue.confirm_cancel('buy-1')
    return session, venue, [ack, pending, second, terminal]


def test_field_roundtrip_and_all_valid_delivery_schedules(tmp_path):
    assert parse_new(new_order(ORDER), INSTRUMENTS) == ORDER
    for index, schedule in enumerate(permutations(range(4))):
        session, venue, reports = prepare(tmp_path / f'order-{index}.sqlite')
        with session:
            for i in schedule:
                session.receive(f'report-{i}', reports[i])
                session.receive(f'duplicate-{i}', reports[i])
            account = session.recover()['snapshot']['account']
            assert (account['cash'], account['inventory']['A'], account['reserved_cash']) == ('304','7','0')
            assert session.recover()['snapshot']['lifecycle']['executions'] == 2
            assert session.reconcile_fix('query', venue.query('buy-1'), now_ns=0, deadline_ns=5)['result']['matched']


@pytest.mark.parametrize('tag,value', [('37','wrong-order'), ('11','unknown-client'), ('151','99'),
                                      ('14','11'), ('6','100'), ('39','2'), ('150','G'), ('38','11')])
def test_invalid_reports_leave_message_book_and_account_unchanged(tmp_path, tag, value):
    session, venue, reports = prepare(tmp_path / 'fix.sqlite')
    with session:
        before = session.recover()
        bad = deepcopy(reports[2])
        bad[tag] = value
        with pytest.raises(ValueError):
            session.receive('invalid', bad)
        assert session.recover() == before


def test_missing_trade_prefix_buffers_without_fabricating_fills_and_survives_restart(tmp_path):
    path = tmp_path / 'fix.sqlite'
    session = TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal)
    venue = SyntheticFixVenue(INSTRUMENTS)
    with session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        ack = venue.new(new_order(session.submit('submit', ORDER, now_ns=0, marks=MARKS)['outgoing']))
        session.receive('ack', ack)
        first = venue.execute('buy-1','4','99')
        second = venue.execute('buy-1','3','100')
        assert session.receive('gap',second)['result']['buffered']
        assert session.recover()['snapshot']['account']['reserved_cash'] == '1000'
        assert session.reconcile_fix('gap-query', venue.query('buy-1'), now_ns=5, deadline_ns=5)['result']['escalated']
    with TradingSession.open(path, journal_type=FixJournal) as session:
        assert session.diagnostics()['mode'] == 'RECOVERY'
        assert session.recover()['snapshot']['operations']['fix']['gaps'] == ['buy-1']
        with pytest.raises(ValueError):
            session.control('unsafe','NORMAL',now_ns=5,reason='reconnected-is-not-reconciled')
        session.receive('redelivered-first',first)
        assert session.recover()['snapshot']['account']['cash'] == '304'
        assert session.reconcile_fix('complete-query',venue.query('buy-1'),now_ns=5,deadline_ns=5)['result']['matched']
        assert session.diagnostics()['unresolved_incidents'] == 0
        session.control('resume','NORMAL',now_ns=5,reason='verified')


def test_pending_cancel_not_terminal_and_terminal_cancel_reject(tmp_path):
    path = tmp_path / 'fix.sqlite'
    session = TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal)
    venue = SyntheticFixVenue(INSTRUMENTS)
    with session:
        session.control('normal','NORMAL',now_ns=0,reason='empty')
        session.receive('ack',venue.new(new_order(session.submit('submit',ORDER,now_ns=0,marks=MARKS)['outgoing'])))
        pending = venue.request_cancel(session.cancel_fix('cancel','buy-1','cancel-1')['outgoing'])
        session.receive('pending',pending)
        assert session.recover()['snapshot']['account']['reserved_cash'] == '1000'
        trade = venue.execute('buy-1','10','100')
        reject = venue.confirm_cancel('buy-1')
        with pytest.raises(ValueError, match='prior terminal'):
            session.cancel_rejected('early-reject',reject)
        session.receive('fill',trade)
        session.cancel_rejected('reject',reject)
        assert session.recover()['snapshot']['lifecycle']['orders']['buy-1']['cancel_status'] == 'too_late'
        bad = deepcopy(trade); bad['31'] = '99'
        with pytest.raises(ValueError,match='Conflicting FIX ExecID'):
            session.receive('conflicting-exec',bad)


@pytest.mark.parametrize('boundary', ['before_snapshot', 'before_commit', 'after_commit'])
def test_fix_book_and_execution_commit_together_on_process_exit(tmp_path, boundary):
    import json
    import subprocess
    import sys
    path = tmp_path / 'crash.sqlite'
    venue = SyntheticFixVenue(INSTRUMENTS)
    with TradingSession.create(path, INSTRUMENTS, POLICY, journal_type=FixJournal) as session:
        session.control('normal','NORMAL',now_ns=0,reason='empty')
        session.receive('ack',venue.new(new_order(session.submit('submit',ORDER,now_ns=0,marks=MARKS)['outgoing'])))
    first = venue.execute('buy-1','4','99')
    script = '''
import os, json, sys
from market_microstructure.trading_session import TradingSession
from market_microstructure.fix_adapter import FixJournal
session = TradingSession.open(sys.argv[1],journal_type=FixJournal)
store = session.journal
original = store._write_snapshot
if sys.argv[2] == 'before_snapshot':
    store._write_snapshot = lambda snapshot: os._exit(78)
elif sys.argv[2] == 'before_commit':
    def crash(snapshot):
        original(snapshot)
        os._exit(78)
    store._write_snapshot = crash
else:
    class Connection:
        def __getattr__(self, key): return getattr(connection, key)
        def commit(self):
            connection.commit()
            os._exit(78)
    connection = store._connection
    store._connection = Connection()
session.receive('trade',json.loads(sys.argv[3]))
raise AssertionError('not crashed')
'''
    run = subprocess.run([sys.executable,'-c',script,str(path),boundary,json.dumps(first)],capture_output=True,timeout=10)
    assert run.returncode == 78, run.stderr
    with TradingSession.open(path,journal_type=FixJournal) as session:
        snapshot = session.recover()['snapshot']
        committed = boundary == 'after_commit'
        assert snapshot['account']['cash'] == ('604' if committed else '1000')
        assert ('execution-1' in snapshot['operations']['fix']['messages']) == committed
        assert session.receive('trade',first)['status'] == ('duplicate' if committed else 'committed')
        assert session.recover()['snapshot']['account']['cash'] == '604'


def test_one_command_fix_e2e_with_real_sigkill(tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable,str(root/'examples/fix_order_recovery.py')],cwd=tmp_path,
                         text=True,capture_output=True,timeout=20)
    assert run.returncode == 0, run.stderr
    assert 'client SIGKILL=-9' in run.stdout
    assert 'cash=304, reserved=0, filled=7, canceled=3' in run.stdout
    assert 'Clean restart also RECOVERY' in run.stdout


def test_new_order_rejection_releases_only_confirmed_order_reservation(tmp_path):
    from market_microstructure.fix_adapter import execution_fields
    with TradingSession.create(tmp_path/'reject.sqlite',INSTRUMENTS,POLICY,journal_type=FixJournal) as session:
        session.control('normal','NORMAL',now_ns=0,reason='empty')
        outgoing = session.submit('submit',ORDER,now_ns=0,marks=MARKS)['outgoing']
        venue = SyntheticFixVenue(INSTRUMENTS)
        message = execution_fields(venue.venue.submit(outgoing,rejection_reason='synthetic-reject'))
        session.receive('rejected',message)
        snapshot = session.recover()['snapshot']
        assert snapshot['lifecycle']['orders']['buy-1']['status'] == 'rejected'
        assert snapshot['account']['cash'] == '1000' and snapshot['account']['reserved_cash'] == '0'
