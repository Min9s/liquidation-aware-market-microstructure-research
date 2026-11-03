"""Optional local Parquet I/O and private hash-pinned replay reports. No network."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .historical import replay_history, validate_policy

DATASETS = {
    "wallet_account_snapshots": ("accounts", ["timestamp", "ingested_at", "requested_at", "source",
        "timestamp_basis", "snapshot_id", "wallet", "scoped_position_count"]),
    "wallet_position_snapshots": ("positions", ["timestamp", "ingested_at", "source", "timestamp_basis",
        "snapshot_id", "wallet", "coin", "signed_size", "liquidation_px"]),
    "asset_ctx": ("contexts", ["timestamp", "ingested_at", "source", "timestamp_basis", "coin",
        "event_id", "session_id", "mid_px", "mark_px", "open_interest"]),
    "bbo": ("bbo", ["timestamp", "ingested_at", "source", "timestamp_basis", "coin", "event_id",
        "session_id", "bid_px", "ask_px", "bid_sz", "ask_sz"]),
}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _private_target(path):
    original = Path(path).absolute()
    target = original.resolve()
    for candidate in (original, target):
        if any((parent / ".git").exists() for parent in (candidate, *candidate.parents)):
            raise ValueError("Historical manifests/results must stay outside every Git repository")
    if target.exists():
        raise ValueError("Refusing to overwrite an existing output")
    return target


def _mkdir_private(path):
    missing = []
    path = Path(path)
    while not path.exists():
        missing.append(path); path = path.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700)


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        Path(path).chmod(0o600)
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def create_manifest(raw_root, policy, destination):
    """Pin native files without copying them or selecting a final account roster."""
    validate_policy(policy)
    destination = _private_target(destination)
    raw_root = Path(raw_root).resolve()
    if not raw_root.is_dir():
        raise ValueError("Local raw root does not exist")
    if destination.is_relative_to(raw_root):
        raise ValueError("Cannot write a manifest inside the read-only raw root")
    low = max(0, policy["start_ns"] - max(policy["snapshot_age_ns"], policy["market_age_ns"]))
    hour_ns = 3600 * 10**9
    entries = []
    for dataset in DATASETS:
        found = set()
        for hour in range(low // hour_ns, policy["end_ns"] // hour_ns + 1):
            clock = datetime.fromtimestamp(hour * 3600, timezone.utc)
            coin = "*" if dataset.startswith("wallet_") else policy["asset"]
            found.update((raw_root / dataset).glob(f"{coin}/{clock:%Y-%m-%d/%H}/*.parquet"))
        if not found:
            raise ValueError(f"No local files for {dataset}")
        for path in sorted(found):
            file = path.resolve()
            if not file.is_relative_to(raw_root):
                raise ValueError("Input escapes the selected raw root")
            entries.append({"dataset": dataset, "path": str(file),
                            "size": file.stat().st_size, "sha256": _sha(file)})
    manifest = {"schema": "historical-replay-input-v1", "raw_root": str(raw_root),
                "policy": dict(policy), "files": entries, "receipt_scope_start_ns": low}
    _mkdir_private(destination.parent)
    _write_json(destination, manifest)
    return {"manifest_sha256": _sha(destination), "files": len(entries)}


def load_manifest(path, expected_sha256):
    """Verify pinned bytes before/after reading; normalize only in memory."""
    path = Path(path).resolve()
    if _sha(path) != expected_sha256:
        raise ValueError("Manifest SHA-256 mismatch")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["schema"] != "historical-replay-input-v1":
        raise ValueError("Unknown historical input schema")
    policy = manifest["policy"]
    validate_policy(policy)
    low = max(0, policy["start_ns"] - max(policy["snapshot_age_ns"], policy["market_age_ns"]))
    if manifest["receipt_scope_start_ns"] != low:
        raise ValueError("Input receipt scope differs from policy")
    root = Path(manifest["raw_root"]).resolve()
    paths = [e["path"] for e in manifest["files"]]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate input file")
    if set(e["dataset"] for e in manifest["files"]) != set(DATASETS):
        raise ValueError("Missing or unknown input dataset")
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as error:
        raise ImportError("Install the optional historical extra for Parquet I/O") from error
    data = {v[0]: [] for v in DATASETS.values()}
    for entry in manifest["files"]:
        file = Path(entry["path"]).resolve()
        if not file.is_relative_to(root):
            raise ValueError("Input path outside raw root")
        if file.stat().st_size != entry["size"] or _sha(file) != entry["sha256"]:
            raise ValueError("Pinned input changed")
        kind, columns = DATASETS[entry["dataset"]]
        table = pq.ParquetFile(file).read(columns=columns)
        if set(columns) - set(table.column_names):
            raise ValueError("Required native columns missing")
        for name in ("timestamp", "ingested_at", "requested_at"):
            if name in table.column_names:
                table = table.set_column(table.schema.get_field_index(name), name, pc.cast(table[name], pa.int64()))
        if _sha(file) != entry["sha256"]:
            raise ValueError("Input changed during reading")
        for row in table.to_pylist():
            if row["source"] != policy["source"]:
                raise ValueError("Native source differs from manifest")
            if type(row["ingested_at"]) is not int:
                raise ValueError("Invalid native receipt timestamp")
            if low <= row["ingested_at"] <= policy["end_ns"]:
                row["source_kind"] = policy["source_kind"]
                data[kind].append(row)
    return data, manifest


def run_manifest(path, expected_sha256, output):
    output = _private_target(output)
    data, manifest = load_manifest(path, expected_sha256)
    if output.is_relative_to(Path(manifest["raw_root"]).resolve()):
        raise ValueError("Cannot write results inside the read-only raw root")
    result = replay_history(data, manifest["policy"])
    code_dir = Path(__file__).resolve().parent
    code_hashes = {p.name: _sha(p) for p in sorted(code_dir.glob("*.py"))}
    gaps = {}
    for kind in ("contexts", "bbo"):
        times = sorted(set(r["ingested_at"] for r in data[kind]))
        gaps[kind] = [{"last_received_ns": a, "next_received_ns": b, "gap_ns": b - a}
                      for a, b in zip(times, times[1:]) if b - a > manifest["policy"]["market_age_ns"]]
    report = {k: v for k, v in result.items() if k != "series"}
    report.update({"schema": "historical-replay-report-v1", "manifest_sha256": expected_sha256,
        "input_file_counts": dict(Counter(e["dataset"] for e in manifest["files"])),
        "input_bytes": sum(e["size"] for e in manifest["files"]), "source_code_sha256": code_hashes,
        "market_receipt_gaps": gaps,
        "runtime": {"python": platform.python_version(), "pyarrow": importlib.metadata.version("pyarrow")},
        "limits": ["Observed successful account sample only; no final roster applied backwards",
            "Receipt warmup bounds the sample; older unobserved accounts are outside scope",
            "Snapshots repeat across decisions; exclusion categories overlap across time",
            "Last market state is usable only within its explicit age bound; gaps remain gaps",
            "No UTC certification, full-market coverage, event labels, orders or profit claims"],
        "prediction_validated": False, "utc_certified": False, "orders_sent": 0})
    _mkdir_private(output)
    _write_json(output / "input_manifest.json", manifest)
    with (output / "series.csv").open("x", newline="", encoding="utf-8") as stream:
        (output / "series.csv").chmod(0o600)
        writer = csv.DictWriter(stream, fieldnames=list(result["series"][0])); writer.writeheader()
        for row in result["series"]:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})
    _write_json(output / "report.json", report)
    frozen = output / "code" / "src" / "market_microstructure"
    _mkdir_private(frozen)
    for file in code_dir.glob("*.py"):
        target = frozen / file.name
        target.write_bytes(file.read_bytes()); target.chmod(0o600)
    p, s = manifest["policy"], result["summary"]
    utc = lambda ns: datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()
    text = (f"# Private historical exposure replay\n\nSource kind: {p['source_kind']}; asset: {p['asset']}\n\n"
        f"UTC decisions: {utc(p['start_ns'])} to {utc(p['end_ns'])}\n\n"
        f"Points: {s['decision_points']}; available: {s['eligible_points']}; excluded: {s['excluded_points']}\n\n"
        f"Unique used accounts: {s['used_unique_accounts']}; snapshots: {s['used_unique_snapshots']}; "
        f"BTC position observations: {s['used_unique_btc_positions']}\n\n"
        f"Input counts: {json.dumps(result['input_counts'], sort_keys=True)}\n\n"
        f"Integrity: {json.dumps(result['integrity'], sort_keys=True)}\n\n"
        f"Snapshot evaluations (repeated): {json.dumps(s['snapshot_evaluations'], sort_keys=True)}\n\n"
        f"Unavailable intervals: {json.dumps(s['unavailable_decision_intervals'])}\n\n"
        f"Statistics: {json.dumps(s['statistics'], sort_keys=True)}\n\n"
        "series.csv retains every decision and its exclusions. This is an observed account sample.\n\n"
        "Reproduce with frozen code: set PYTHONPATH to this run's code/src, invoke "
        "python -B -m market_microstructure.historical_io replay with input_manifest.json, "
        f"--manifest-sha256 {expected_sha256}, and a NEW private output directory.\n\n"
        "No event study, forecasting, orders or profitability interpretation.\n")
    (output / "report.md").write_text(text, encoding="utf-8"); (output / "report.md").chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pin = commands.add_parser("pin")
    pin.add_argument("--raw-root", required=True); pin.add_argument("--policy", required=True)
    pin.add_argument("--manifest", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--manifest", required=True); replay.add_argument("--manifest-sha256", required=True)
    replay.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "pin":
        result = create_manifest(args.raw_root, json.loads(Path(args.policy).read_text()), args.manifest)
    else:
        report = run_manifest(args.manifest, args.manifest_sha256, args.output)
        result = {"summary": report["summary"], "integrity": report["integrity"]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
