"""FIX field objects, surviving venue, actual client SIGKILL; no FIX session."""
import json
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory

from market_microstructure.fix_adapter import FixJournal, new_order
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession
from durable_order_recovery import (_inputs, _request, _require, _execution_facts, _venue_ledger, _assert_ledger)

WORKER = '''
import json,sys
from market_microstructure.fix_adapter import FixJournal
from market_microstructure.trading_session import TradingSession
instruments, policy = json.loads(sys.argv[2])
with TradingSession.create(sys.argv[1],instruments,policy,journal_type=FixJournal) as session:
    for line in sys.stdin:
        request=json.loads(line)
        method=request.pop('method')
        if method not in ('control','submit','receive','cancel_fix','recover'):
            raise ValueError('Unsupported fixture request')
        print(json.dumps(getattr(session,method)(**request)),flush=True)
'''


def main():
    instruments, policy, marks, order = _inputs()
    venue = SyntheticFixVenue(instruments)
    print('SYNTHETIC_TEST_ONLY — FIX 4.4 field objects; no wire/session/network; manual fills; zero fees')
    with TemporaryDirectory(prefix='fix-recovery-') as directory:
        path = Path(directory) / 'fix.sqlite'
        process = subprocess.Popen([sys.executable,'-u','-B','-c',WORKER,str(path),json.dumps([instruments,policy])],
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            _request(process,'control',command_id='normal',mode='NORMAL',now_ns=0,reason='empty-validated-account')
            prepared = _request(process,'submit',command_id='submit',order=order,now_ns=0,marks=marks)
            print('NewOrderSingle fields=' + json.dumps(new_order(prepared['outgoing'])))
            ack = venue.new(new_order(prepared['outgoing']))  # ACK withheld.
            print('35=D committed before dispatch; 35=8/150=0 acknowledgement withheld')
            first = venue.execute('buy-a','4','99')
            _request(process,'receive',command_id='first',message=first)
            cancel = _request(process,'cancel_fix',command_id='cancel',client_order_id='buy-a',cancel_id='cancel-a')['outgoing']
            print('Partial Trade fields=' + json.dumps(first))
            print('OrderCancelRequest fields=' + json.dumps(cancel))
            pending = venue.request_cancel(cancel)
            print('Pending Cancel fields (withheld)=' + json.dumps(pending))
            venue.execute('buy-a','3','100')
            venue.confirm_cancel('buy-a')  # Cancel connection lost: retain local reservation.
            before = _request(process,'recover')
            _require(before['snapshot']['account']['reserved_cash'] == '600','Premature reservation release')
            process.kill()
            _, stderr = process.communicate(timeout=10)
            _require(process.returncode == -signal.SIGKILL,stderr)
            print('35=F sent; 3 additional fills and cancellation response lost; client SIGKILL=-9')
        finally:
            if process.poll() is None:
                process.kill(); process.communicate(timeout=10)
            for pipe in (process.stdin,process.stdout,process.stderr):
                pipe.close()
        with TradingSession.open(path,journal_type=FixJournal) as session:
            _require(session.diagnostics()['mode'] == 'RECOVERY','Startup did not gate risk')
            _require(session.recover()['snapshot']['account'] == before['snapshot']['account'],'Restart changed accounting')
            _require(session.submit('submit',order,now_ns=0,marks=marks)['outgoing'] is None,'Original order resent')
            query = session.reconcile_fix('incomplete-query',venue.query('buy-a'),now_ns=5,deadline_ns=5)
            _require(query['result']['escalated'],'Missing FIX facts did not escalate')
            print('Startup RECOVERY; status query alone cannot identify missing Trade ExecID; Incident OPEN')
            for index, message in enumerate(reversed(venue.redeliver())):
                session.receive(f'redelivery-{index}',message)
                session.receive(f'duplicate-{index}',message)
            session.reconcile_fix('complete-query',venue.query('buy-a'),now_ns=6,deadline_ns=5)
            session.control('resume','NORMAL',now_ns=6,reason='complete-trade-history-and-fresh-status')
            final = session.recover()
            facts = _execution_facts((venue.venue.query('buy-a'),))
            oracle = _venue_ledger(instruments,policy['initial_cash'],facts)
            _assert_ledger(final,oracle)
            _require(oracle == dict(cash='304',inventory={'SYNTH-A':'7'},executions=2),'Independent oracle differs')
            local = final['snapshot']['lifecycle']['orders']['buy-a']
            _require((local['status'],local['filled_quantity'],local['remaining_quantity']) == ('canceled','7','3'),'Wrong order terminal facts')
            _require(session.diagnostics()['unresolved_incidents'] == 0,'Incident unresolved')
            print('Manual archive redelivery (not FIX Resend) + fresh status: cash=304, reserved=0, filled=7, canceled=3; NORMAL')
        with TradingSession.open(path,journal_type=FixJournal) as session:
            _require(session.recover()['snapshot']['account'] == final['snapshot']['account'],'Final restart changed accounting')
            _require(session.diagnostics()['mode'] == 'RECOVERY','Clean restart bypassed recovery')
            print('Clean restart also RECOVERY; final order/account unchanged')


if __name__ == '__main__':
    main()
