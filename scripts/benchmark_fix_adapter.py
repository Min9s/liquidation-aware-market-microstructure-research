"""Pure FIX field conversion only; no journal, fsync, session or network timing."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter_ns

from market_microstructure.execution_contracts import KIND
from market_microstructure.fix_adapter import new_order, parse_new, cancel_request, decode_history
from market_microstructure.synthetic_fix import SyntheticFixVenue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    instruments = {'A': dict(price_tick='1', quantity_step='1')}
    order = dict(source_kind=KIND,client_order_id='buy-1',symbol='A',side='buy',quantity='10',limit_price='100')
    venue = SyntheticFixVenue(instruments)
    messages = [venue.new(new_order(order)),venue.execute('buy-1','4','99')]
    messages.append(venue.request_cancel(cancel_request(order,'cancel-1')))
    messages += [venue.execute('buy-1','3','100'),venue.confirm_cancel('buy-1')]
    def roundtrip():
        if parse_new(new_order(order),instruments) != order:
            raise AssertionError('roundtrip differs')
    def convert():
        reports, gap = decode_history(order,messages,{'cancel-1':'buy-1'},instruments,'synthetic-venue')
        if gap or reports[-1]['state']['status'] != 'canceled':
            raise AssertionError('conversion differs')
    for _ in range(50):
        roundtrip(); convert()
    raw = []
    for _ in range(1000):
        start=perf_counter_ns(); roundtrip(); first=perf_counter_ns()-start
        start=perf_counter_ns(); convert(); second=perf_counter_ns()-start
        raw.append(dict(new_order_roundtrip_ns=first, five_report_conversion_ns=second))
    summary = {}
    for key in raw[0]:
        values=sorted(r[key] for r in raw)
        summary[key]=dict(p50_ns=statistics.median(values),p95_ns=values[math.ceil(.95*len(values))-1],
                          p99_ns=values[math.ceil(.99*len(values))-1],min_ns=min(values),max_ns=max(values))
    import market_microstructure.fix_adapter as module
    result=dict(source_kind=KIND,python=sys.version,platform=platform.platform(),samples=1000,warmup=50,
                adapter_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                interval='perf_counter_ns wall time; functions include assertions; fixture creation excluded; five-report conversion reconstructs full ledger; excludes SQLite, journal replay, venue, startup and network',
                percentile='nearest rank p95/p99; descriptive local run, not a tail guarantee',raw=raw,summary=summary)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
