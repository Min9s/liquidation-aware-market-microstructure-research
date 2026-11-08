import json
import subprocess
import sys

import pytest

from market_microstructure.trading_session import TradingSession
from market_microstructure.simulated_venue import SimulatedVenue
from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER


def test_clean_restart_always_requires_fresh_authoritative_queries(tmp_path):
    path = tmp_path / 'session.sqlite'
    venue = SimulatedVenue(INSTRUMENTS)
    with TradingSession.create(path, INSTRUMENTS, POLICY) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty-account')
        venue.submit(session.submit('submit', ORDER, now_ns=0, marks=MARKS)['outgoing'])
    with TradingSession.open(path) as session:
        ops = session.recover()['snapshot']['operations']
        assert ops['mode'] == 'RECOVERY' and ops['session']['previous_exit'] == 'clean'
        assert session.submit('probe', dict(ORDER, client_order_id='probe', quantity='1', limit_price='1'),
                              now_ns=0, marks=MARKS)['outgoing'] is None
        with pytest.raises(ValueError, match='fresh matched'):
            session.control('unsafe', 'NORMAL', now_ns=0, reason='not-yet-queried')
        session.reconcile_at('query', 'buy-1', venue.query('buy-1'), now_ns=0, deadline_ns=5)
        session.control('safe', 'NORMAL', now_ns=0, reason='verified')
        assert session.diagnostics()['mode'] == 'NORMAL'


def test_second_process_is_excluded_and_crash_during_recovery_keeps_gate(tmp_path):
    path = tmp_path / 'session.sqlite'
    with TradingSession.create(path, INSTRUMENTS, POLICY) as session:
        script = '''
import sys
from market_microstructure.trading_session import TradingSession
try:
    TradingSession.open(sys.argv[1])
except BlockingIOError:
    sys.exit(75)
raise AssertionError('second owner admitted')
'''
        run = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True, timeout=10)
        assert run.returncode == 75, run.stderr
    crash = '''
import os, sys
from market_microstructure.trading_session import TradingSession
session = TradingSession.open(sys.argv[1])
os._exit(76)
'''
    run = subprocess.run([sys.executable, '-c', crash, str(path)], capture_output=True, timeout=10)
    assert run.returncode == 76
    with TradingSession.open(path) as session:
        assert session.diagnostics()['mode'] == 'RECOVERY'
        assert session.recover()['snapshot']['operations']['session']['previous_exit'] == 'unclean-or-first-start'
        assert session.submit('blocked', ORDER, now_ns=0, marks=MARKS)['result']['reason'] == 'operational_recovery'


def test_missing_exchange_retains_reservation_without_timeout_escape(tmp_path):
    path = tmp_path / 'session.sqlite'
    with TradingSession.create(path, INSTRUMENTS, POLICY) as session:
        session.control('normal', 'NORMAL', now_ns=0, reason='empty')
        session.submit('submit', ORDER, now_ns=0, marks=MARKS)
    with TradingSession.open(path) as session:
        session.reconcile_at('missing', 'buy-1', None, now_ns=100, deadline_ns=5)
        assert session.recover()['snapshot']['account']['reserved_cash'] == '1000'
        with pytest.raises(ValueError, match='Unresolved'):
            session.control('unsafe', 'NORMAL', now_ns=100, reason='timeout-is-not-evidence')


@pytest.mark.parametrize('boundary', ['before_snapshot', 'before_commit', 'after_commit'])
def test_startup_commit_crash_never_returns_an_unsafe_session(tmp_path, boundary):
    path = tmp_path / 'startup.sqlite'
    with TradingSession.create(path, INSTRUMENTS, POLICY) as session:
        session.control('normal','NORMAL',now_ns=0,reason='empty')
        session.submit('submit',ORDER,now_ns=0,marks=MARKS)
    script = '''
import os, sys
from market_microstructure.oms_operations import OperationalJournal
from market_microstructure.trading_session import TradingSession
class Faulty(OperationalJournal):
    @classmethod
    def open(cls, path):
        store = super().open(path)
        original = store._write_snapshot
        if sys.argv[2] == 'before_snapshot':
            store._write_snapshot = lambda snapshot: os._exit(77)
        elif sys.argv[2] == 'before_commit':
            def crash(snapshot):
                original(snapshot)
                os._exit(77)
            store._write_snapshot = crash
        else:
            class Connection:
                def __getattr__(self, key): return getattr(store_connection, key)
                def commit(self):
                    store_connection.commit()
                    os._exit(77)
            store_connection = store._connection
            store._connection = Connection()
        return store
TradingSession.open(sys.argv[1], journal_type=Faulty)
raise AssertionError('startup returned before recovery commit')
'''
    run = subprocess.run([sys.executable,'-c',script,str(path),boundary],capture_output=True,timeout=10)
    assert run.returncode == 77, run.stderr
    with TradingSession.open(path) as session:
        snapshot = session.recover()['snapshot']
        assert snapshot['operations']['mode'] == 'RECOVERY'
        assert snapshot['operations']['verified'] == {}
        assert snapshot['account']['reserved_cash'] == '1000'
        assert snapshot['account']['cash'] == '1000'
        with pytest.raises(ValueError,match='fresh matched'):
            session.control('unsafe','NORMAL',now_ns=0,reason='unverified')
