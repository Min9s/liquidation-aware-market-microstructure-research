"""Bounded synthetic full-replay benchmark; no latency thresholds or venue traffic."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from market_microstructure.execution_contracts import KIND
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[10, 50, 150])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--active-orders', type=int, default=0)
    parser.add_argument('--fills-per-order', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.active_orders <= 20 or not 0 <= args.fills_per_order <= 10:
        parser.error('active-orders 0..20; fills-per-order 0..10')
    if args.repeats < 2 or any(n < 1 or n > 500 for n in args.sizes):
        parser.error('use >=2 repeats and sizes 1..500')
    root = Path(__file__).resolve().parents[1]
    instruments = {'A': dict(price_tick='1', quantity_step='1')}
    policy = dict(source_kind=KIND, initial_cash='1000000', session_id='bench', max_quote_age_ns=10,
                  risk_limits=dict(max_order_notional='1000000', max_symbol_gross={'A': '1000000'},
                                   max_portfolio_gross='1000000'))
    marks = {'A': dict(quote_id='q', timestamp_ns=0, available_ns=0, session_id='bench', valid=True, price='1')}
    rows = []
    with TemporaryDirectory() as directory:
        for size in args.sizes:
            for repeat in range(args.repeats):
                path = Path(directory) / f'{size}-{repeat}.sqlite'
                store = FundedJournal.create(path, instruments, policy)
                # Seed distinct rejected decisions: history grows, live order state stays bounded.
                rejected = dict(source_kind=KIND, client_order_id='seed', symbol='A', side='buy', quantity='1', limit_price='1')
                for i in range(size):
                    store.submit(f'seed-{i}', rejected, now_ns=0, marks={})
                venue = SimulatedVenue(instruments)
                for active in range(args.active_orders):
                    key = f'active-{active}'
                    outgoing = store.submit(key, dict(rejected, client_order_id=key, quantity='20'),
                                            now_ns=0, marks=marks)['outgoing']
                    venue.submit(outgoing)
                    for fill_index in range(args.fills_per_order):
                        store.apply(f'{key}-fill-{fill_index}', venue.execute(key, '1', '1'))
                history = store.recover()['committed_commands']
                verification = []
                original_verify = store._verify
                def timed_verify():
                    start_verify = perf_counter_ns()
                    value = original_verify()
                    verification.append(perf_counter_ns() - start_verify)
                    return value
                store._verify = timed_verify
                spec = dict(rejected, client_order_id='accepted')
                start = perf_counter_ns()
                result = store.submit('accepted', spec, now_ns=0, marks=marks)
                submit = perf_counter_ns() - start
                if not result['result']['accepted']:
                    raise AssertionError('benchmark order rejected')
                venue.submit(result['outgoing'])
                report = venue.execute('accepted', '1', '1')
                start = perf_counter_ns()
                store.apply('fill', report)
                apply = perf_counter_ns() - start
                submit_verify, apply_verify = verification
                start = perf_counter_ns()
                store.recover()
                recover = perf_counter_ns() - start
                settings = {p: store._connection.execute(f'PRAGMA {p}').fetchone()[0]
                            for p in ('journal_mode', 'synchronous', 'busy_timeout', 'page_size')}
                store.close()
                start = perf_counter_ns()
                with FundedJournal.open(path) as reopened:
                    pass  # open includes full verification; recover would perform another replay.
                recovery = perf_counter_ns() - start
                rows.append(dict(seed_history=size, history=history, active_orders=args.active_orders,
                    fills_per_active_order=args.fills_per_order, repeat=repeat, submit_ns=submit,
                    apply_ns=apply, open_ns=recovery, recover_ns=recover,
                    submit_verify_ns=submit_verify, apply_verify_ns=apply_verify))
    summary = []
    for size in args.sizes:
        subset = [r for r in rows if r['seed_history'] == size]
        summary.append(dict(seed_history=size, history=subset[0]['history'],
            active_orders=args.active_orders, fills_per_active_order=args.fills_per_order,
            **{key: dict(median_ns=statistics.median(r[key] for r in subset),
            p95_nearest_rank_ns=sorted(r[key] for r in subset)[math.ceil(.95 * len(subset))-1],
            p99_nearest_rank_ns=(sorted(r[key] for r in subset)[math.ceil(.99 * len(subset))-1]
                                 if len(subset) >= 100 else None),
            min_ns=min(r[key] for r in subset), max_ns=max(r[key] for r in subset))
            for key in ('submit_ns', 'apply_ns', 'open_ns', 'recover_ns', 'submit_verify_ns', 'apply_verify_ns')}))
    result = dict(source_kind=KIND, python=sys.version, platform=platform.platform(), sqlite=sqlite3.sqlite_version,
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        git_status=subprocess.check_output(['git', 'status', '--short'], cwd=root, text=True),
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'src/market_microstructure').glob('*.py')},
        benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        settings=settings, repeats=args.repeats, percentile_note='p95 nearest rank; small-sample descriptive only; p99 suppressed below 100 samples', workload='N rejected submits + A active orders with F partial fills each; accepted probe submit; full-fill apply; recover; close/open',
        timing='perf_counter_ns wall time; seed/venue/close excluded; open includes verify; no warmup; local temporary filesystem',
        raw=rows, summary=summary)
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
