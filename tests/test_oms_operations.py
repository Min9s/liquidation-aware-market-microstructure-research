from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
import sys
from threading import Barrier

import pytest

from market_microstructure.oms_operations import OperationalJournal
from market_microstructure.simulated_venue import SimulatedVenue
from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER


def create(path):
    store = OperationalJournal.create(path, INSTRUMENTS, POLICY)
    store.control('normal', 'NORMAL', now_ns=0, reason='empty-validated-account')
    return store


def seed(store):
    venue = SimulatedVenue(INSTRUMENTS)
    outgoing = store.submit('submit', ORDER, now_ns=0, marks=MARKS)['outgoing']
    venue.submit(outgoing)
    first = venue.execute('buy-1', '4', '99')
    store.apply('four', first)
    store.request_cancel('cancel', 'buy-1')
    second = venue.execute('buy-1', '3', '100')
    terminal = venue.cancel('buy-1')
    return venue, first, second, terminal


def test_incident_lifecycle_dedup_risk_gate_and_authoritative_resolution(tmp_path):
    path = tmp_path / 'ops.sqlite'
    with create(path) as store:
        venue, first, second, terminal = seed(store)
        before = store.recover()['snapshot']['account']
        lag = store.inspect('transient', 'buy-1', terminal, now_ns=1, deadline_ns=5)
        assert not lag['result']['escalated']
        assert not store.recover()['snapshot']['operations']['incidents']
        result = store.inspect('overdue', 'buy-1', terminal, now_ns=5, deadline_ns=5)
        key = result['result']['incident_id']
        store.inspect('redetected', 'buy-1', venue.query('buy-1'), now_ns=6, deadline_ns=5)
        state = store.recover()
        assert state['snapshot']['account'] == before
        assert state['snapshot']['operations']['mode'] == 'RESTRICTED'
        assert len(state['snapshot']['operations']['incidents']) == 1
        with pytest.raises(ValueError, match='Unresolved'):
            store.control('unsafe', 'NORMAL', now_ns=6, reason='premature')
        denied = store.submit('probe', dict(ORDER, client_order_id='probe', quantity='1', limit_price='1'), now_ns=6, marks=MARKS)
        assert denied['result']['reason'] == 'operational_restricted' and denied['outgoing'] is None
        # Existing fills still apply, but delivery alone cannot close the incident.
        store.apply('seven', second)
        assert store.recover()['snapshot']['account']['reserved_cash'] == '300'
        assert store.diagnostics()['unresolved_incidents'] == 1
        store.investigate('investigate', key, now_ns=7, reason='fetch-authoritative-query')
    with OperationalJournal.open(path) as store:
        assert store.diagnostics()['unresolved_incidents'] == 1
        store.control('recovery', 'RECOVERY', now_ns=7, reason='operator-recovery')
        store.reconcile_at('terminal', 'buy-1', venue.query('buy-1'), now_ns=8, deadline_ns=5)
        final = store.recover()['snapshot']
        incident = final['operations']['incidents'][key]
        assert [h['status'] for h in incident['history']] == ['OPEN', 'INVESTIGATING', 'RESOLVED']
        assert final['account']['cash'] == '304' and final['account']['reserved_cash'] == '0'
        assert final['account']['inventory'] == {'A': '7'}
        for index, report in enumerate((terminal, second, first, first)):
            store.apply(f'late-{index}', report)
        with pytest.raises(ValueError, match='fresh matched'):
            store.control('stale-proof', 'NORMAL', now_ns=8, reason='premature')
        store.reconcile_at('recheck', 'buy-1', venue.query('buy-1'), now_ns=9, deadline_ns=5)
        store.control('normal-again', 'NORMAL', now_ns=9, reason='all-queries-validated')
        assert store.diagnostics('buy-1')['unresolved_incidents'] == 0
        final = store.recover()
    with OperationalJournal.open(path) as store:
        assert store.recover() == final


def test_conflict_needs_operator_investigation_not_just_a_good_query(tmp_path):
    with create(tmp_path / 'ops.sqlite') as store:
        venue, first, second, terminal = seed(store)
        bad = deepcopy(first)
        bad['event_id'] = 'conflicting-authority'
        bad['state']['fills'][0]['price'] = '98'
        result = store.reconcile_at('bad-query', 'buy-1', bad, now_ns=1, deadline_ns=5)
        assert result['result']['kind'] == 'conflicting_facts'
        key = result['result']['incident_id']
        assert store.recover()['snapshot']['account']['cash'] == '604'
        store.reconcile_at('good-query', 'buy-1', terminal, now_ns=2, deadline_ns=5)
        assert store.diagnostics()['unresolved_incidents'] == 1
        store.investigate('investigate', key, now_ns=2, reason='discard-corrupt-fixture-query')
        store.reconcile_at('verified-query', 'buy-1', terminal, now_ns=3, deadline_ns=5)
        assert store.diagnostics()['unresolved_incidents'] == 0


@pytest.mark.parametrize('operation', ['inspect', 'reconcile_at'])
@pytest.mark.parametrize('boundary', ['before_snapshot', 'before_commit', 'after_commit'])
def test_process_exit_cannot_separate_incident_mode_or_account(tmp_path, operation, boundary):
    path = tmp_path / 'ops.sqlite'
    with create(path) as store:
        venue, _, _, terminal = seed(store)
        if operation == 'reconcile_at':
            store.inspect('overdue', 'buy-1', terminal, now_ns=5, deadline_ns=5)
        before = store.recover()
    script = '''
import json, os, sys
from market_microstructure.oms_operations import OperationalJournal
store = OperationalJournal.open(sys.argv[1])
original = store._write_snapshot
if sys.argv[3] == 'before_snapshot':
    store._write_snapshot = lambda snapshot: os._exit(74)
elif sys.argv[3] == 'before_commit':
    def crash(snapshot):
        original(snapshot)
        os._exit(74)
    store._write_snapshot = crash
else:
    class ExitAfterCommit:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, key): return getattr(self.connection, key)
        def commit(self):
            self.connection.commit()
            os._exit(74)
    store._connection = ExitAfterCommit(store._connection)
getattr(store, sys.argv[2])('injected', 'buy-1', json.loads(sys.argv[4]), now_ns=5, deadline_ns=5)
raise AssertionError('exit hook not reached')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(path), operation, boundary, json.dumps(terminal)],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 74, result.stderr
    with OperationalJournal.open(path) as store:
        current = store.recover()
        if boundary != 'after_commit':
            assert current == before
        else:
            ops = current['snapshot']['operations']
            if operation == 'inspect':
                assert ops['mode'] == 'RESTRICTED'
                assert len(ops['incidents']) == 1
                assert current['snapshot']['account'] == before['snapshot']['account']
            else:
                assert next(iter(ops['incidents'].values()))['status'] == 'RESOLVED'
                assert current['snapshot']['account']['cash'] == '304'
                assert current['snapshot']['account']['reserved_cash'] == '0'
        retry = getattr(store, operation)('injected', 'buy-1', terminal, now_ns=5, deadline_ns=5)
        assert retry['status'] == ('duplicate' if boundary == 'after_commit' else 'committed')


def test_mode_and_submit_are_serialized_across_connections(tmp_path):
    path = tmp_path / 'ops.sqlite'
    with create(path):
        pass
    barrier = Barrier(2)
    def attempt(action):
        with OperationalJournal.open(path) as store:
            barrier.wait(timeout=5)
            if action == 'restrict':
                return store.control('restrict', 'RESTRICTED', now_ns=0, reason='operator-stop')
            return store.submit('submit', ORDER, now_ns=0, marks=MARKS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restrict, submitted = list(pool.map(attempt, ['restrict', 'submit']))
    with OperationalJournal.open(path) as store:
        status = store.diagnostics()
        events = status['recent_events']
        restriction_seq = next(e['sequence'] for e in events if e['command_id'] == 'restrict')
        submit_seq = next(e['sequence'] for e in events if e['command_id'] == 'submit')
        assert submitted['result']['accepted'] == (submit_seq < restriction_seq)
        assert status['mode'] == 'RESTRICTED'
        assert status['snapshot']['account']['reserved_cash'] == ('1000' if submit_seq < restriction_seq else '0')


def test_absent_query_keeps_reservations_and_normal_requires_all_orders(tmp_path):
    path = tmp_path / 'ops.sqlite'
    with create(path) as store:
        store.submit('submit', ORDER, now_ns=0, marks=MARKS)
        store.reconcile_at('missing', 'buy-1', None, now_ns=5, deadline_ns=5)
        status = store.diagnostics()
        assert status['snapshot']['account']['reserved_cash'] == '1000'
        assert status['recovery_required'] == ['buy-1']
        assert status['unresolved_incidents'] == 1
        with pytest.raises(ValueError, match='Unresolved'):
            store.control('premature', 'NORMAL', now_ns=5, reason='unsafe')
        with pytest.raises(ValueError, match='reconcile_at'):
            store.reconcile('bypass', 'buy-1', None)


def test_restricted_allows_cancel_reports_and_inventory_reduction(tmp_path):
    with create(tmp_path / 'ops.sqlite') as store:
        venue = SimulatedVenue(INSTRUMENTS)
        ack = venue.submit(store.submit('submit', ORDER, now_ns=0, marks=MARKS)['outgoing'])
        store.apply('ack', ack)
        store.control('stop', 'RESTRICTED', now_ns=1, reason='operator-stop')
        store.request_cancel('cancel', 'buy-1')
        fill = venue.execute('buy-1', '4', '99')
        store.apply('fill', fill)
        assert store.recover()['snapshot']['account']['reserved_cash'] == '600'
        # Inventory-backed sell reduces exposure and still passes the original funding check.
        sale = dict(ORDER, client_order_id='sell-1', side='sell', quantity='1', limit_price='100')
        assert store.submit('sale', sale, now_ns=1, marks={})['result']['accepted']
        oversell = dict(sale, client_order_id='oversell', quantity='4')
        assert store.submit('oversell', oversell, now_ns=1, marks={})['result']['reason'] == 'insufficient_inventory'
        with pytest.raises(ValueError, match='fresh matched'):
            store.control('unsafe-normal', 'NORMAL', now_ns=1, reason='not-reconciled')


def test_operational_schema_and_snapshot_validation_are_not_bypassed(tmp_path):
    import sqlite3
    from market_microstructure.funded_journal import FundedJournal
    path = tmp_path / 'ops.sqlite'
    with create(path):
        pass
    with pytest.raises(ValueError, match='schema'):
        FundedJournal.open(path)
    with sqlite3.connect(path) as connection:
        snapshot = json.loads(connection.execute('SELECT payload FROM snapshot').fetchone()[0])
        snapshot['operations']['mode'] = 'RESTRICTED'
        connection.execute('UPDATE snapshot SET payload=?', (json.dumps(snapshot, sort_keys=True, separators=(',', ':')),))
    with pytest.raises(ValueError, match='snapshot differs'):
        OperationalJournal.open(path)


def test_e2e_real_kill_incident_and_cli_order_trace(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / 'incident.sqlite'
    run = subprocess.run([sys.executable, str(root / 'examples/incident_recovery.py'), '--db', str(path)],
                         cwd=tmp_path, text=True, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert 'SYNTHETIC_TEST_ONLY' in run.stdout
    assert 'cash=1000-4*99-3*100=304' in run.stdout
    state = subprocess.run([sys.executable, '-m', 'market_microstructure.oms_operations', str(path), '--order', 'buy-a'],
                           cwd=tmp_path, text=True, capture_output=True, timeout=10)
    assert state.returncode == 0, state.stderr
    status = json.loads(state.stdout)
    assert status['mode'] == 'NORMAL' and status['unresolved_incidents'] == 0
    incident = next(iter(status['snapshot']['operations']['incidents'].values()))
    assert incident['status'] == 'RESOLVED'
    assert incident['execution_ids'] == ['execution-1', 'execution-2']
    assert status['recent_events']
    with OperationalJournal.open(path) as store:
        assert store.diagnostics('buy')['recent_events'] == []  # exact identifier matching
