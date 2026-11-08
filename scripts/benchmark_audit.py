"""Current implementation audit: isolated local cases; no trading latency claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from market_microstructure.execution_contracts import KIND
from market_microstructure.fix_adapter import FixJournal, decode_history, new_order
from market_microstructure.order_lifecycle import OrderManager
from market_microstructure.synthetic_fix import SyntheticFixVenue
from market_microstructure.trading_session import TradingSession


def timed(call):
    start=perf_counter_ns(); result=call()
    return perf_counter_ns()-start,result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--repeats',type=int,default=20)
    args=parser.parse_args()
    if not 2 <= args.repeats <= 100:
        parser.error('repeats must be 2..100')
    instruments={'A':dict(price_tick='1',quantity_step='1')}
    policy=dict(source_kind=KIND,initial_cash='1000000',session_id='audit',max_quote_age_ns=10,
                risk_limits=dict(max_order_notional='1000000',max_symbol_gross={'A':'1000000'},max_portfolio_gross='1000000'))
    marks={'A':dict(quote_id='q',timestamp_ns=0,available_ns=0,session_id='audit',valid=True,price='100')}
    def order(key):
        return dict(source_kind=KIND,client_order_id=key,symbol='A',side='buy',quantity='10',limit_price='100')
    profiles=[('history10',10,1,1,0),('history50',50,1,1,0),('history150',150,1,1,0),
              ('active5',50,5,1,0),('fills3',50,1,3,0),('duplicates',50,1,1,20)]
    raw=[]
    with TemporaryDirectory(prefix='audit-benchmark-') as directory:
        for name,history,active,fills,duplicates in profiles:
            for repeat in range(args.repeats):
                path=Path(directory)/f'{name}-{repeat}.sqlite'
                session=TradingSession.create(path,instruments,policy,journal_type=FixJournal)
                store=session.journal
                venue=SyntheticFixVenue(instruments)
                session.control('normal','NORMAL',now_ns=0,reason='empty')
                last=None
                for i in range(active):
                    key=f'active-{i}'
                    venue.new(new_order(session.submit(key,order(key),now_ns=0,marks=marks)['outgoing']))
                    for j in range(fills):
                        last=venue.execute(key,'1','99')
                        session.receive(f'{key}-fill-{j}',last)
                for i in range(duplicates):
                    session.receive(f'duplicate-{i}',last)
                count=session.recover()['committed_commands']
                if count>history:
                    raise AssertionError('profile cannot fit target history')
                for i in range(history-count):
                    session.submit(f'padding-{i}',order(f'denied-{i}'),now_ns=0,marks={})
                if session.recover()['committed_commands']!=history:
                    raise AssertionError('incorrect seeded history')
                sample=dict(profile=name,history=history,active_orders=active,fills_per_order=fills,
                            duplicate_commands=duplicates,duplicate_fraction=duplicates/history,repeat=repeat)
                def verify():
                    store._connection.execute('BEGIN')
                    try:
                        store._verify()
                        store._connection.commit()
                    except BaseException:
                        store._connection.rollback(); raise
                sample['verify_ns'],_=timed(verify)
                sample['submit_ns'],prepared=timed(lambda:session.submit('probe',order('probe'),now_ns=0,marks=marks))
                if not prepared['result']['accepted']:
                    raise AssertionError('probe rejected')
                venue.new(new_order(prepared['outgoing']))
                report=venue.execute('probe','1','99')
                native=venue.venue.query('probe')
                memory=OrderManager(instruments); memory.submit(order('probe'))
                sample['state_ns'],_=timed(lambda:memory.apply(native))
                sample['fix_ns'],conversion=timed(lambda:decode_history(order('probe'),[report],{},instruments,'synthetic-venue'))
                if conversion[1]:
                    raise AssertionError('unexpected FIX gap')
                sample['report_ns'],_=timed(lambda:session.receive('probe-fill',report))
                query=venue.query('probe')
                sample['reconcile_ns'],matched=timed(lambda:session.reconcile_fix('probe-query',query,now_ns=0,deadline_ns=5))
                if not matched['result']['matched']:
                    raise AssertionError('query not matched')
                # Simulated query outage escalates after deadline; do not invent exchange fills.
                sample['incident_ns'],incident=timed(lambda:session.inspect('outage','probe',None,now_ns=5,deadline_ns=5))
                if not incident['result']['incident_id']:
                    raise AssertionError('incident absent')
                settings={p:store._connection.execute(f'PRAGMA {p}').fetchone()[0]
                          for p in ('journal_mode','synchronous','busy_timeout','page_size')}
                session.close()
                sample['startup_ns'],session=timed(lambda:TradingSession.open(path,journal_type=FixJournal))
                if session.diagnostics()['mode']!='RECOVERY':
                    raise AssertionError('startup gate absent')
                session.close()
                raw.append(sample)
    summary=[]
    keys=[key for key in raw[0] if key.endswith('_ns')]
    for name,*_ in profiles:
        rows=[r for r in raw if r['profile']==name]
        metrics={}
        for key in keys:
            values=sorted(r[key] for r in rows); n=len(values)
            import math
            metrics[key]=dict(median_ns=statistics.median(values),stdev_ns=statistics.stdev(values),
                              variance_ns2=statistics.variance(values),min_ns=min(values),max_ns=max(values),
                              p95_ns=values[math.ceil(.95*n)-1],p99_ns=values[math.ceil(.99*n)-1] if n>=100 else None)
        summary.append(dict(profile=name,metrics=metrics))
    root=Path(__file__).resolve().parents[1]
    result=dict(python=sys.version,platform=platform.platform(),cpu=platform.processor(),cpu_count=os.cpu_count(),
                sqlite=sqlite3.sqlite_version,settings=settings,repeats=args.repeats,warmup=0,
                commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
                source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'src/market_microstructure').glob('*.py')},
                script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                timing='perf_counter_ns wall time; sequential fresh cases; seeding and venue excluded; verify at H, submit H, report H+1, reconcile H+2, incident H+3; startup after close H+5; no cache flush; state uses one in-memory snapshot apply; FIX converts one Trade; startup includes lock, open verification, recover verification and start command verification/commit',
                percentile='p95 is descriptive nearest rank; p99 suppressed below 100 samples',raw=raw,summary=summary)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
