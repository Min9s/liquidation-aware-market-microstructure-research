"""Verify published A/B observations or rerun the source-pinned synthetic experiment.

Outputs must be new and outside Git repositories. No user databases are opened.
The two helpers preserve the original timed workload and diagnostic functions.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'benchmarks/immutable-funded-history.json'


def verify_evidence():
    data = json.loads(EVIDENCE.read_text())
    path = ROOT / 'benchmarks/immutable-funded-history.csv'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == data['observations_csv_sha256']
    for name, digest in data['historical_files_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    with path.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    for experiment, result in data['runs'].items():
        rng = random.Random(161803)
        samples = [r for r in rows if r['experiment'] == experiment]
        assert len(samples) == 1792
        assert all(r['correctness'] == r['pair_equivalence'] == 'passed' for r in samples)
        for summary in result['submit_summary']:
            for variant in ['A', 'B']:
                group = [r for r in samples if r['operation'] == 'submit'
                         and r['mode'] == summary['mode'] and int(r['H']) == summary['H']
                         and r['variant'] == variant]
                assert {int(r['repeat']) for r in group} == set(range(21))
                values = sorted(int(r['wall_ns']) / 1e6 for r in group)
                cpu = [int(r['cpu_ns']) / 1e6 for r in group]
                for actual, expected in [(statistics.median(values), summary[variant+'_p50_ms']),
                                         (values[math.ceil(.95*len(values))-1], summary[variant+'_p95_empirical_ms']),
                                         (statistics.median(cpu), summary[variant+'_cpu_p50_ms'])]:
                    assert math.isclose(actual, expected, abs_tol=1e-9)
            paired = {}
            for row in samples:
                if row['operation'] == 'submit' and row['mode'] == summary['mode'] and int(row['H']) == summary['H']:
                    paired.setdefault(row['repeat'], {})[row['variant']] = int(row['wall_ns'])
            changes = [100*(1-p['B']/p['A']) for p in paired.values()]
            boot = sorted(statistics.median(rng.choices(changes, k=len(changes))) for _ in range(20000))
            def quantile(probability):
                index = (len(boot)-1)*probability
                return boot[int(index)]*(1-(index-int(index))) + boot[math.ceil(index)]*(index-int(index))
            assert math.isclose(quantile(.05/24), summary['paired_reduction_low'], abs_tol=1e-9)
            assert math.isclose(quantile(1-.05/24), summary['paired_reduction_high'], abs_tol=1e-9)
        for kind in ['profile', 'memory', 'phases']:
            assert len(result['diagnostics'][kind]) == 72
        print(experiment + ': 1792 observations; submit p50/p95/CPU and bootstrap intervals match')
    print('Six historical benchmark JSON digests unchanged. Projections are not full raw archives.')


def summarize(rows):
    result = []
    for mode, history, operation in sorted({(r['mode'], r['H'], r['operation']) for r in rows}):
        group = [r for r in rows if (r['mode'], r['H'], r['operation']) == (mode, history, operation)]
        row = dict(mode=mode, H=history, operation=operation)
        for variant in ['A', 'B']:
            samples = [r for r in group if r['variant'] == variant]
            values = sorted(r['wall_ns']/1e6 for r in samples)
            row[variant] = dict(samples=len(values), p50_ms=statistics.median(values),
                                empirical_p95_ms=values[math.ceil(.95*len(values))-1],
                                cpu_p50_ms=statistics.median(r['cpu_ns']/1e6 for r in samples))
        row['saved_p50_ms'] = row['A']['p50_ms']-row['B']['p50_ms']
        row['p50_reduction_percent'] = 100*(1-row['B']['p50_ms']/row['A']['p50_ms'])
        result.append(row)
    return result


def prepare(output, smoke):
    output = output.resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new external directory')
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    inside = subprocess.run(['git', '-C', str(parent), 'rev-parse', '--git-dir'],
                            capture_output=True).returncode == 0
    if output.is_relative_to(ROOT) or inside:
        raise ValueError('Output must be outside Git repositories')
    data = json.loads(EVIDENCE.read_text())
    versions = {}
    # Check history before creating output. A shallow checkout may need git fetch --unshallow.
    for commit in [data['baseline_commit'], data['candidate_commit']]:
        subprocess.run(['git', 'cat-file', '-e', commit+'^{commit}'], cwd=ROOT, check=True)
    output.mkdir(parents=True)
    for variant, key in [('A', 'baseline_commit'), ('B', 'candidate_commit')]:
        root = output / variant
        archive = subprocess.check_output(['git', 'archive', data[key], 'src/market_microstructure'], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for member in source:
                parts = Path(member.name).parts
                if member.isdir():
                    continue
                if not member.isfile() or '..' in parts or Path(member.name).is_absolute():
                    raise ValueError('Unexpected archive member')
                target = root / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
        expected = data['source_sha256'][variant]
        for name, digest in expected.items():
            assert hashlib.sha256((root/'src/market_microstructure'/name).read_bytes()).hexdigest() == digest
        versions[variant] = dict(root=str(root), out=str(output/'fixtures'/variant),
                                 commit=data[key], sizes=[10] if smoke else [10, 50, 150, 500],
                                 source_sha256=expected)
    for name, target in [('_history_ab_worker.py', 'worker.py'), ('_history_ab_workload.py', 'fixed_harness.py')]:
        shutil.copyfile(ROOT/'scripts'/name, output/target)
    (output/'variants.json').write_text(json.dumps(versions, indent=2)+'\n')
    return versions


def experiment(output, smoke):
    versions = prepare(output, smoke)
    output = output.resolve()
    rng = random.Random(314159)

    def run(variant, action, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
                   PYTHONPATH=versions[variant]['root']+'/src')
        process = subprocess.run([sys.executable, '-B', str(output/'worker.py'), variant, action, *map(str, args)],
                                 env=env, cwd=output, check=True, capture_output=True, text=True)
        return json.loads(process.stdout)

    def pair(mode, history, operation, kind, repeat):
        order = ['A', 'B']
        rng.shuffle(order)
        rows = [run(v, 'sample', '--mode', mode, '--H', history, '--op', operation,
                    '--kind', kind, '--repeat', repeat) for v in order]
        for key in ['logical_result', 'logical_recovery', 'history_sha256']:
            assert rows[0][key] == rows[1][key], (mode, history, operation, kind, key)
        for row in rows:
            row.update(ab_order=''.join(order), pair_equivalence='passed')
        return sorted(rows, key=lambda r: r['variant'])

    for variant in ['A', 'B']:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
                   PYTHONPATH=versions[variant]['root']+'/src')
        print('Seeding', variant, flush=True)
        with (output/('fixtures-'+variant+'.log')).open('w') as log:
            subprocess.run([sys.executable, '-B', str(output/'worker.py'), variant, 'fixtures'],
                           env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT, check=True)
    compatibility = {v: run(v, 'cross', '--other', 'B' if v == 'A' else 'A') for v in ['A', 'B']}
    (output/'compatibility.json').write_text(json.dumps(compatibility, indent=2)+'\n')
    sizes = versions['A']['sizes']
    operations = ['submit', 'policy_reject', 'invalid_rollback', 'fill', 'reconcile', 'recover', 'validate', 'reopen']
    cells = [(m, h, op) for m in ['native', 'operational', 'fix'] for h in sizes
             for op in operations + (['startup'] if m != 'native' else [])]
    latency = []
    for repeat in range(-1, 1 if smoke else 21):
        schedule = cells.copy() if repeat < (1 if smoke else 7) else [c for c in cells if c[2] == 'submit']
        rng.shuffle(schedule)
        for mode, history, operation in schedule:
            rows = pair(mode, history, operation, 'plain', repeat)
            if repeat >= 0:
                latency.extend(rows)
        (output/'latency.json').write_text(json.dumps(latency, indent=2)+'\n')
        print(time.strftime('%H:%M:%S'), 'latency round', repeat, 'rows', len(latency), flush=True)
    for kind in ['profile', 'memory', 'phases']:
        rows = []
        schedule = [(m, h, 'submit') for m in ['native', 'operational', 'fix'] for h in sizes]
        for repeat in range(1 if smoke else 3):
            rng.shuffle(schedule)
            for mode, history, operation in schedule:
                rows.extend(pair(mode, history, operation, kind, repeat))
            (output/(kind+'.json')).write_text(json.dumps(rows, indent=2)+'\n')
            print(kind, 'round', repeat, flush=True)
    (output/'latency-summary.json').write_text(json.dumps(summarize(latency), indent=2)+'\n')
    print('All logical results, snapshots and histories equivalent.' +
          (' Smoke only: not performance evidence.' if smoke else ' Full protocol completed.'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-evidence', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--smoke', action='store_true', help='H10/one pair; execution check, not benchmark evidence')
    args = parser.parse_args()
    if not __debug__:
        parser.error('Do not disable assertions with -O or PYTHONOPTIMIZE')
    if args.verify_evidence:
        verify_evidence()
    elif args.output:
        experiment(args.output, args.smoke)
    else:
        parser.error('Choose --verify-evidence or --output NEW_EXTERNAL_DIRECTORY')
