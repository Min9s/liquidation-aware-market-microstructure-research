"""Pinned exploratory study and self-contained static HTML/SVG. No external services."""

import argparse
import csv
import hashlib
import io
import json
import math
import platform
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .event_study import LABELS, study_events, validate_rules
from .historical import replay_history
from .historical_io import _mkdir_private, _private_target, _sha, _write_json, load_manifest


def register_study(phase2_directory, rules, destination):
    """Register fixed rules and Phase 2 hashes BEFORE computing price outcomes."""
    validate_rules(rules)
    target = _private_target(destination)
    phase2 = Path(phase2_directory).resolve()
    report = json.loads((phase2 / "report.json").read_text())
    if _sha(phase2 / "input_manifest.json") != report["manifest_sha256"]:
        raise ValueError("Phase 2 input manifest changed")
    protocol = {"schema": "pinned-event-study-v1", "registered_at_utc": datetime.now(timezone.utc).isoformat(),
                "phase2_directory": str(phase2), "phase2_report_sha256": _sha(phase2 / "report.json"),
                "phase2_series_sha256": _sha(phase2 / "series.csv"),
                "phase2_manifest_sha256": report["manifest_sha256"],
                "source_kind": report["policy"]["source_kind"], "rules": rules}
    _mkdir_private(target.parent); _write_json(target, protocol)
    return _sha(target)


def _utc(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%H:%M:%S")


def _line_svg(points, title, start, end, max_gap, limits=None):
    valid = [v for _, v in points if v is not None and math.isfinite(v)]
    if not valid:
        return f"<p>{escape(title)}: no observations</p>"
    low, high = limits if limits else (min(valid), max(valid))
    if high == low:
        low, high = low - 1, high + 1
    x = lambda t: 85 + (t - start) / max(end - start, 1) * 850
    y = lambda v: 220 - (v - low) / (high - low) * 170
    parts = [f'<svg viewBox="0 0 1000 280" role="img" aria-label="{escape(title)}">',
             f'<text x="85" y="24">{escape(title)}</text>']
    for fraction in (0, 0.5, 1):
        v = low + (high - low) * fraction
        parts += [f'<line x1="85" y1="{y(v):.2f}" x2="935" y2="{y(v):.2f}" stroke="#ddd"/>',
                  f'<text x="5" y="{y(v)+5:.2f}">{v:.6g}</text>']
    segments, current, previous = [], [], None
    for t, value in points:
        if value is None or not math.isfinite(value) or (previous is not None and t - previous > max_gap):
            if current: segments.append(current)
            current = []
        if value is not None and math.isfinite(value):
            current.append(f"{x(t):.2f},{y(value):.2f}")
        previous = t
    if current: segments.append(current)
    for segment in segments:
        if len(segment) == 1:
            cx, cy = segment[0].split(','); parts.append(f'<circle cx="{cx}" cy="{cy}" r="2" fill="#176b8f"/>')
        else:
            parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="#176b8f" stroke-width="1.5"/>')
    for t in (start, (start + end) // 2, end):
        parts.append(f'<text x="{x(t)-30:.2f}" y="252">{_utc(t)}</text>')
    return ''.join(parts) + '</svg>'


def _frequency_svg(horizons):
    groups = [(h, b) for h, summary in horizons.items() for b in summary['lgi_bins']]
    width = 105
    parts = ['<svg viewBox="0 0 1000 350" role="img" aria-label="Conditional first-passage frequencies">',
             '<text x="65" y="24">LGI conditional frequencies: upper / lower / neither</text>']
    colors = {'upper_first': '#24856b', 'lower_first': '#c66b36', 'neither': '#b0b7bf'}
    for i, (h, b) in enumerate(groups):
        x, bottom = 75 + i * width, 245
        for label in LABELS:
            height = (b['frequencies'][label] or 0) * 180
            parts.append(f'<rect x="{x}" y="{bottom-height:.2f}" width="65" height="{height:.2f}" fill="{colors[label]}"/>')
            bottom -= height
        parts += [f'<text x="{x}" y="268">{h}s</text>', f'<text x="{x}" y="290">{escape(b["bin"])}</text>',
                  f'<text x="{x}" y="312">n={b["valid_outcomes"]}</text>']
    return ''.join(parts) + '</svg>'


def _exclusions_svg(horizons):
    bars = [(f'{h}s: {reason}', n) for h, s in horizons.items() for reason, n in s['exclusion_reasons'].items()]
    if not bars:
        return '<p>No excluded outcomes.</p>'
    height = 65 + 42 * len(bars); maximum = max(n for _, n in bars)
    parts = [f'<svg viewBox="0 0 1000 {height}" role="img" aria-label="Excluded outcomes">',
             '<text x="15" y="24">Excluded/censored outcomes (absolute counts)</text>']
    for i, (label, n) in enumerate(bars):
        y = 50 + 42 * i
        parts += [f'<text x="15" y="{y+17}">{escape(label)}</text>',
                  f'<rect x="410" y="{y}" width="{n/maximum*480:.2f}" height="24" fill="#bb764d"/>',
                  f'<text x="920" y="{y+17}">{n}</text>']
    return ''.join(parts) + '</svg>'


def _table(bins):
    result = ['<table><tr><th>Bin</th><th>Anchors</th><th>Valid</th><th>Excluded</th>',
              '<th>Upper first</th><th>Lower first</th><th>Neither</th><th>Mean mid change (bps)</th></tr>']
    for b in bins:
        cells = [escape(b['bin']), str(b['candidate_anchors']), str(b['valid_outcomes']), str(b['excluded_outcomes'])]
        for label in LABELS:
            rate = b['frequencies'][label]
            cells.append(f"{b['label_counts'][label]} ({rate:.1%})" if rate is not None else '0 (—)')
        cells.append(f"{b['mean_return_bps']:.4f}" if b['mean_return_bps'] is not None else '—')
        result.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
    return ''.join(result) + '</table>'


def render_report(result, features, contexts, protocol):
    p, rules = result['phase2_policy'], result['rules']
    prices = []
    for row in sorted(contexts, key=lambda r: r['ingested_at']):
        t = row['ingested_at']
        if not p['start_ns'] <= t <= p['end_ns']: continue
        try:
            value = float(row['mid_px'])
            if value <= 0 or not math.isfinite(value) or row['session_id'] != p['session_id']: value = None
        except (TypeError, ValueError): value = None
        prices.append((t, value))
    html = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>Exploratory LGI Event Study</title>',
        '<style>body{font:15px system-ui;max-width:1100px;margin:36px auto;padding:0 20px;color:#24313c}svg{width:100%;background:#fafbfc;margin:12px 0}svg text{font:14px system-ui;fill:#24313c}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{padding:7px;border:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}code{overflow-wrap:anywhere}</style>',
        '<h1>Exploratory LGI / observed-price event study</h1>',
        f'<p><strong>{escape(result["source_kind"])}</strong> · BTC · {datetime.fromtimestamp(p["start_ns"]/1e9,timezone.utc).isoformat()} — {datetime.fromtimestamp(p["end_ns"]/1e9,timezone.utc).isoformat()}</p>',
        '<p>Descriptive conditional frequencies only. One session, overlapping anchors, no IID inference, model fit or trading claim.</p>',
        f'<p>Rules registered before outcomes: <code>{escape(protocol["registered_at_utc"])}</code>. Symmetric barriers ±{rules["barrier_bps"]}bps; full horizon required; gap/age ≤{rules["max_gap_ns"]/1e9:g}s. Same first upper/lower receipt is ambiguous. End prices use the last fresh future observation, never a later price.</p>',
        '<p>Context mid uses receipt/state timestamps; exchange-clock accuracy is unverified. These are sampled observed first hits, not continuous-time crossings or liquidation events.</p>',
        _line_svg([(r['decision_ns'],r['lgi'] if r['status']=='available' else None) for r in features],
                  'LGI at decision time',p['start_ns'],p['end_ns'],p['step_ns'],(-1,1)),
        _line_svg(prices,'Observed context mid (UTC receipt time)',p['start_ns'],p['end_ns'],rules['max_gap_ns']),
        _frequency_svg(result['horizons']), _exclusions_svg(result['horizons'])]
    for h, s in result['horizons'].items():
        n = s['nonoverlap_sensitivity']
        html += [f'<h2>{h}-second horizon</h2>',
            f'<p>Valid {s["valid_outcomes"]}/{s["candidate_anchors"]}; censored {s["censored"]}; ambiguous {s["ambiguous"]}; excluded {s["excluded_fraction"]:.2%}. Overlapping forward-window components: {s["overlapping_forward_window_components"]} (not independent sample counts).</p>',
            '<h3>LGI: all registered anchors</h3>', _table(s['lgi_bins']),
            f'<h3>OBI-only descriptive comparison: common valid cohort n={s["common_valid_outcomes"]}</h3>',
            '<p>Same outcome rows, fixed bins, no fitted prediction model. Different bin populations cannot establish which indicator predicts better.</p>',
            '<h4>LGI, common cohort</h4>',_table(s['lgi_bins_common']),
            '<h4>OBI, common cohort</h4>',_table(s['obi_bins_common']),
            f'<h3>Start-aligned non-overlapping forward windows</h3><p>Valid {n["valid_outcomes"]}/{n["candidate_anchors"]}. Adjacent windows still share market conditions, reference information and account snapshots; independence is not asserted.</p>',
            _table(n['lgi_bins'])]
    html += ['<h2>Reproduction and limitations</h2><p>Use protocol.json and its externally recorded SHA with the frozen code under code/src and a NEW private output directory. result.json records Phase 2 input/CSV/report hashes and study code hashes.</p>',
        '<p>No p-values, confidence intervals, significance, causal interpretation, out-of-sample performance or profitability claims. Missing labels remain null; observation-end exclusions remain in the denominator. Account sampling and excluded liquidation thresholds remain limitations inherited from Phase 2.</p></html>']
    return ''.join(html)


def run_study(protocol_path, expected_sha256, output):
    output = _private_target(output)
    protocol_path = Path(protocol_path).resolve()
    protocol_bytes = protocol_path.read_bytes()
    if hashlib.sha256(protocol_bytes).hexdigest() != expected_sha256: raise ValueError('Study protocol SHA mismatch')
    protocol = json.loads(protocol_bytes)
    if protocol['schema'] != 'pinned-event-study-v1': raise ValueError('Unknown study protocol')
    validate_rules(protocol['rules'])
    p2 = Path(protocol['phase2_directory'])
    for name, key in (('report.json','phase2_report_sha256'),('series.csv','phase2_series_sha256'),
                      ('input_manifest.json','phase2_manifest_sha256')):
        if _sha(p2/name) != protocol[key]: raise ValueError('Phase 2 artifact changed')
    report = json.loads((p2/'report.json').read_text())
    if report['policy']['source_kind'] != protocol['source_kind']: raise ValueError('Mixed study provenance')
    code_dir = Path(__file__).resolve().parent
    for name, digest in report['source_code_sha256'].items():
        if _sha(code_dir/name) != digest: raise ValueError('Phase 2 implementation changed')
    data, manifest = load_manifest(p2/'input_manifest.json', protocol['phase2_manifest_sha256'])
    if output.is_relative_to(Path(manifest['raw_root']).resolve()): raise ValueError('Read-only raw root')
    features = replay_history(data, report['policy'])['series']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(features[0])); writer.writeheader()
    for row in features:
        writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    if hashlib.sha256(buffer.getvalue().encode()).hexdigest() != protocol['phase2_series_sha256']:
        raise ValueError('Phase 2 numerical replay differs from pinned CSV')
    result = study_events(features,data['contexts'],data['bbo'],report['policy'],protocol['rules'])
    result.update(protocol_sha256=expected_sha256, phase2_series_sha256=protocol['phase2_series_sha256'],
        phase2_manifest_sha256=protocol['phase2_manifest_sha256'], phase2_report_sha256=protocol['phase2_report_sha256'],
        code_sha256={p.name:_sha(p) for p in sorted(code_dir.glob('*.py'))}, python=platform.python_version())
    _mkdir_private(output)
    (output/'protocol.json').write_bytes(protocol_bytes); (output/'protocol.json').chmod(0o600)
    _write_json(output/'result.json',result)
    fields = list(dict.fromkeys(k for row in result['outcomes'] for k in row))
    with (output/'outcomes.csv').open('x',newline='') as stream:
        (output/'outcomes.csv').chmod(0o600)
        writer = csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(result['outcomes'])
    (output/'report.html').write_text(render_report(result,features,data['contexts'],protocol),encoding='utf-8')
    (output/'report.html').chmod(0o600)
    frozen = output/'code/src/market_microstructure'; _mkdir_private(frozen)
    for file in code_dir.glob('*.py'):
        (frozen/file.name).write_bytes(file.read_bytes()); (frozen/file.name).chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command',required=True)
    register = sub.add_parser('register'); register.add_argument('--phase2',required=True)
    register.add_argument('--rules',required=True); register.add_argument('--protocol',required=True)
    run = sub.add_parser('run'); run.add_argument('--protocol',required=True)
    run.add_argument('--protocol-sha256',required=True); run.add_argument('--output',required=True)
    args = parser.parse_args()
    if args.command == 'register':
        print(register_study(args.phase2,json.loads(Path(args.rules).read_text()),args.protocol))
    else:
        result = run_study(args.protocol,args.protocol_sha256,args.output)
        print(json.dumps(result['horizons'],sort_keys=True,allow_nan=False))


if __name__ == '__main__': main()
