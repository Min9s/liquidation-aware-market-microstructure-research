"""Exhaustive delivery schedules, not parallel execution or random sampling."""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations
import json
import subprocess
import sys

import pytest

from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue
from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER, create, submit


def check(snapshot, venue_report):
    """Independent arithmetic from authoritative cumulative venue facts."""
    order = snapshot['lifecycle']['orders']['buy-1']
    account = snapshot['account']
    fills = venue_report['state']['fills']
    ids = {f['execution_id']: f for f in fills}
    assert len(ids) == len(fills)
    quantity = sum(Decimal(f['quantity']) for f in ids.values())
    cost = sum(Decimal(f['quantity']) * Decimal(f['price']) for f in ids.values())
    assert Decimal(order['filled_quantity']) == quantity
    assert Decimal(account['cash']) == 1000 - cost
    assert Decimal(account['inventory']['A']) == quantity
    assert snapshot['lifecycle']['executions'] == len(ids)
    reserve = 0 if order['status'] in {'filled', 'canceled', 'rejected'} else (10 - quantity) * 100
    assert Decimal(account['reserved_cash']) == reserve
    assert Decimal(account['available_cash']) == 1000 - cost - reserve
    assert Decimal(account['available_cash']) >= 0


def test_all_race_delivery_orders_and_restart_cuts(tmp_path):
    # Four cumulative reports: ack, 4 fills, 7 fills, canceled remainder.
    # Cancel is requested after first 4 fills are locally known.
    final_states = []
    for schedule in permutations(range(4)):
        for cut in range(5):
            path = tmp_path / ('race-' + ''.join(map(str, schedule)) + f'-{cut}.sqlite')
            venue = SimulatedVenue(INSTRUMENTS)
            store = create(path)
            try:
                ack = venue.submit(submit(store)['outgoing'])
                first = venue.execute('buy-1', '4', '99')
                store.apply('first', first)
                check(store.recover()['snapshot'], first)
                store.request_cancel('cancel', 'buy-1')
                second = venue.execute('buy-1', '3', '100')
                terminal = venue.cancel('buy-1')
                reports = [ack, first, second, terminal]
                highest = first
                for index in range(5):
                    if index == cut:
                        before = store.recover()
                        store.close()
                        store = FundedJournal.open(path)
                        assert store.recover() == before
                    if index == 4:
                        break
                    report = reports[schedule[index]]
                    if report['state']['revision'] > highest['state']['revision']:
                        highest = report
                    store.apply(f'delivery-{index}', report)
                    snapshot = store.recover()['snapshot']
                    check(snapshot, highest)
                    store.apply(f'duplicate-{index}', report)
                    assert store.recover()['snapshot'] == snapshot
                    assert store.apply(f'delivery-{index}', report)['outgoing'] is None
                final = store.recover()
                record = final['snapshot']['lifecycle']['orders']['buy-1']
                assert (record['status'], record['cancel_status'], record['filled_quantity'], record['remaining_quantity']) == ('canceled', 'canceled', '7', '3')
                assert final['snapshot']['account']['cash'] == '304'  # 1000 - 4*99 - 3*100
                assert not final['reconciliation']
                final_states.append(final['snapshot'])
            finally:
                store.close()
    assert all(state == final_states[0] for state in final_states)


@pytest.mark.parametrize('boundary', ['before_snapshot', 'before_commit', 'after_commit'])
def test_exit_during_terminal_reconciliation_is_atomic_and_retryable(tmp_path, boundary):
    path = tmp_path / 'reconcile.sqlite'
    venue = SimulatedVenue(INSTRUMENTS)
    with create(path) as store:
        venue.submit(submit(store)['outgoing'])
        store.apply('four', venue.execute('buy-1', '4', '99'))
        store.request_cancel('cancel', 'buy-1')
        before = store.recover()
    venue.execute('buy-1', '3', '100')
    terminal = venue.cancel('buy-1')
    script = '''
import json, os, sys
from market_microstructure.funded_journal import FundedJournal
store = FundedJournal.open(sys.argv[1])
original = store._write_snapshot
if sys.argv[2] == 'before_snapshot':
    store._write_snapshot = lambda snapshot: os._exit(73)
elif sys.argv[2] == 'before_commit':
    def crash(snapshot):
        original(snapshot)
        os._exit(73)
    store._write_snapshot = crash
else:
    class ExitAfterCommit:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self, key): return getattr(self.connection, key)
        def commit(self):
            self.connection.commit()
            os._exit(73)
    store._connection = ExitAfterCommit(store._connection)
store.reconcile('terminal-query', 'buy-1', json.loads(sys.argv[3]))
raise AssertionError('exit hook not reached')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(path), boundary, json.dumps(terminal)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 73, result.stderr
    with FundedJournal.open(path) as store:
        if boundary != 'after_commit':
            assert store.recover() == before
        retry = store.reconcile('terminal-query', 'buy-1', terminal)
        assert retry['status'] == ('duplicate' if boundary == 'after_commit' else 'committed')
        check(store.recover()['snapshot'], terminal)
        final = store.recover()
        assert not final['reconciliation']
    with FundedJournal.open(path) as store:
        assert store.recover() == final


def test_unresolved_cancel_and_contradictory_authority_fail_closed(tmp_path):
    venue = SimulatedVenue(INSTRUMENTS)
    with create(tmp_path / 'orders.sqlite') as store:
        ack = venue.submit(submit(store)['outgoing'])
        store.apply('four', venue.execute('buy-1', '4', '99'))
        store.request_cancel('cancel', 'buy-1')
        store.reconcile('absent', 'buy-1', None)
        unresolved = store.recover()
        assert unresolved['reconciliation'][0]['cancel_status'] == 'unknown'
        assert unresolved['snapshot']['account']['reserved_cash'] == '600'
        # A stale open query is found=True but cannot resolve the cancel outcome.
        store.reconcile('stale-open', 'buy-1', ack)
        assert store.recover()['reconciliation'][0]['cancel_status'] == 'unknown'
        venue.execute('buy-1', '3', '100')
        terminal = venue.cancel('buy-1')
        store.reconcile('terminal', 'buy-1', terminal)
        before = store.recover()
        corrupt = deepcopy(terminal)
        corrupt['event_id'] = 'conflicting-authority'
        corrupt['state']['fills'][0]['price'] = '98'
        with pytest.raises(ValueError, match='Conflicting'):
            store.reconcile('conflict', 'buy-1', corrupt)
        assert store.recover() == before
