"""A surviving synthetic venue, killed OMS client and persistent incident recovery."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory

from market_microstructure.oms_operations import OperationalJournal
from market_microstructure.simulated_venue import SimulatedVenue
from durable_order_recovery import (_inputs, _request, _require, _execution_facts,
                                    _venue_ledger, _assert_ledger)

WORKER = '''
import json, sys
from market_microstructure.oms_operations import OperationalJournal
instruments, policy = json.loads(sys.argv[2])
with OperationalJournal.create(sys.argv[1], instruments, policy) as store:
    for line in sys.stdin:
        request = json.loads(line)
        method = request.pop('method')
        if method not in ('submit', 'apply', 'request_cancel', 'recover', 'control'):
            raise ValueError('Unsupported demo request')
        print(json.dumps(getattr(store, method)(**request)), flush=True)
'''


def run_case(path):
    if os.name != 'posix':
        raise RuntimeError('Demo requires POSIX SIGKILL and pipes')
    instruments, policy, marks, order = _inputs()
    venue = SimulatedVenue(instruments)
    process = subprocess.Popen([sys.executable, '-u', '-B', '-c', WORKER, str(path), json.dumps([instruments, policy])],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timeline = []
    def phase(label, state):
        snapshot = state['snapshot']
        timeline.append(dict(phase=label, account=snapshot['account'],
                             order=snapshot['lifecycle']['orders'].get('buy-a'), operations=snapshot['operations']))
    try:
        _request(process, 'control', command_id='initial-normal', mode='NORMAL', now_ns=0, reason='empty-account-validated')
        outgoing = _request(process, 'submit', command_id='submit', order=order, now_ns=0, marks=marks)['outgoing']
        phase('submit committed: cash reserved before dispatch', _request(process, 'recover'))
        ack = venue.submit(outgoing)
        first = venue.execute('buy-a', '4', '99')
        _request(process, 'apply', command_id='first-four', report=first)
        phase('first 4@99 applied', _request(process, 'recover'))
        _request(process, 'request_cancel', command_id='cancel', client_order_id='buy-a')
        phase('cancel requested: remaining 6 stays reserved', _request(process, 'recover'))
        second = venue.execute('buy-a', '3', '100')
        terminal = venue.cancel('buy-a')  # Both replies withheld: venue 7/canceled; local 4/cancel pending.
        before = _request(process, 'recover')
        phase('response-loss: local 4, venue 7 and canceled; reservation still 600', before)
        process.kill()
        _, stderr = process.communicate(timeout=10)
        _require(process.returncode == -signal.SIGKILL, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
    with OperationalJournal.open(path) as store:
        _require(store.recover() == before, 'Restart changed committed facts')
        store.control('begin-recovery', 'RECOVERY', now_ns=1, reason='client-restart')
        result = store.inspect('within-grace', 'buy-a', terminal, now_ns=1, deadline_ns=5)
        _require(not result['result']['escalated'], 'Transient lag created an incident')
        # No authoritative query was ingested by deadline. Detect with an explicit saved venue observation.
        result = store.inspect('expired-comparison', 'buy-a', terminal, now_ns=5, deadline_ns=5)
        key = result['result']['incident_id']
        _require(key is not None, 'Overdue real mismatch was not recorded')
        phase('deadline exceeded: persisted incident and RESTRICTED atomically', store.recover())
        store.inspect('repeat-detection', 'buy-a', venue.query('buy-a'), now_ns=5, deadline_ns=5)
        _require(store.diagnostics()['unresolved_incidents'] == 1, 'Duplicate incident')
        rejected = store.submit('new-risk', dict(order, client_order_id='probe', quantity='1', limit_price='1'),
                                now_ns=5, marks=marks)
        _require(rejected['outgoing'] is None and rejected['result']['reason'] == 'operational_restricted',
                 'New risk was not restricted')
        store.investigate('investigate', key, now_ns=6, reason='operator-fetches-complete-query')
        store.control('recovery', 'RECOVERY', now_ns=6, reason='query-recovery')
        store.reconcile_at('terminal-query', 'buy-a', venue.query('buy-a'), now_ns=7, deadline_ns=5)
        phase('query applies missing fill and releases canceled remainder; incident resolved', store.recover())
        for index, report in enumerate((terminal, second, first, ack, second)):
            store.apply(f'late-{index}', report)
        store.reconcile_at('final-query', 'buy-a', venue.query('buy-a'), now_ns=8, deadline_ns=5)
        store.control('resume', 'NORMAL', now_ns=8, reason='all-incidents-resolved-and-orders-verified')
        final = store.recover()
        oracle = _venue_ledger(instruments, policy['initial_cash'], _execution_facts((first, second)))
        _assert_ledger(final, oracle)
        _require(oracle == dict(cash='304', inventory={'SYNTH-A': '7'}, executions=2), 'Wrong independent oracle')
        _require(not final['reconciliation'] and store.diagnostics()['unresolved_incidents'] == 0, 'Unresolved recovery')
        phase('NORMAL restored; independent ledger matches', final)
    with OperationalJournal.open(path) as store:
        _require(store.recover() == final, 'Final reopen differs')
    return dict(source_kind='SYNTHETIC_TEST_ONLY', timeline=timeline, final=final, oracle=oracle,
                client_exit_code=process.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, help='retain a NEW database for CLI inspection; never overwrites')
    args = parser.parse_args()
    if args.db:
        result = run_case(args.db.absolute())
    else:
        with TemporaryDirectory(prefix='oms-incident-') as directory:
            result = run_case(Path(directory) / 'oms.sqlite')
    print('SYNTHETIC_TEST_ONLY — manual fills, zero fees; actual child SIGKILL; logical escalation deadline=5ns')
    print(f"Client exit={result['client_exit_code']}")
    for phase in result['timeline']:
        print(json.dumps(phase, ensure_ascii=False))
    print('Independent ledger: cash=1000-4*99-3*100=304, inventory=7, reserved=0; incident RESOLVED; NORMAL')


if __name__ == '__main__':
    main()
