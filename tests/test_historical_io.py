import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow", reason="Parquet integration tests require the historical extra")
import pyarrow.parquet as pq

from examples.historical_synthetic import synthetic_inputs
from market_microstructure.historical import replay_history
from market_microstructure.historical_io import DATASETS, create_manifest, load_manifest, run_manifest


def native_fixture(tmp_path):
    data, policy = synthetic_inputs()
    root = tmp_path / "raw"
    for dataset, (kind, columns) in DATASETS.items():
        groups = defaultdict(list)
        for row in data[kind]:
            clock = datetime.fromtimestamp(row["ingested_at"] / 1e9, timezone.utc)
            coin = row.get("coin", "ALL")
            groups[(coin, clock.strftime("%Y-%m-%d/%H"))].append({k: row[k] for k in columns})
        for i, ((coin, clock), rows) in enumerate(groups.items()):
            path = root / dataset / coin / clock / f"synthetic-{i}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(rows)
            for name in ("timestamp", "ingested_at", "requested_at"):
                if name in table.column_names:
                    table = table.set_column(table.schema.get_field_index(name), name, table[name].cast(pa.timestamp("ns", tz="UTC")))
            pq.write_table(table, path)
    manifest = tmp_path / "private" / "input.json"
    pin = create_manifest(root, policy, manifest)
    return data, policy, manifest, pin["manifest_sha256"]


def test_parquet_round_trip_exact_integer_ns_and_frozen_replay(tmp_path):
    data, policy, manifest, digest = native_fixture(tmp_path)
    loaded, _ = load_manifest(manifest, digest)
    assert replay_history(loaded, policy) == replay_history(data, policy)
    out = tmp_path / "result"
    report = run_manifest(manifest, digest, out)
    assert report["summary"]["eligible_points"] == 3
    assert (out / "code/src/market_microstructure/historical.py").exists()
    assert "synthetic-long" not in (out / "series.csv").read_text()
    assert (out.stat().st_mode & 0o777) == 0o700
    assert ((out / "series.csv").stat().st_mode & 0o777) == 0o600
    second = tmp_path / "result-again"
    assert run_manifest(manifest, digest, second) == report
    assert (out / "series.csv").read_bytes() == (second / "series.csv").read_bytes()


def test_changed_native_bytes_are_rejected(tmp_path):
    _, _, manifest, digest = native_fixture(tmp_path)
    path = Path(json.loads(manifest.read_text())["files"][0]["path"])
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="changed"):
        load_manifest(manifest, digest)


def test_manifest_modification_is_rejected(tmp_path):
    _, _, manifest, digest = native_fixture(tmp_path)
    manifest.write_text(manifest.read_text() + " ")
    with pytest.raises(ValueError, match="Manifest SHA"):
        load_manifest(manifest, digest)


def test_new_files_do_not_enter_pinned_inventory(tmp_path):
    _, _, manifest, digest = native_fixture(tmp_path)
    original, pin = load_manifest(manifest, digest)
    file = Path(pin["files"][0]["path"])
    (file.parent / "added-after-pin.parquet").write_bytes(file.read_bytes())
    assert load_manifest(manifest, digest)[0] == original


def test_duplicate_file_and_path_escape_are_rejected(tmp_path):
    _, _, manifest, _ = native_fixture(tmp_path)
    pin = json.loads(manifest.read_text()); pin["files"].append(dict(pin["files"][0]))
    manifest.write_text(json.dumps(pin)); digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Duplicate"):
        load_manifest(manifest, digest)
    pin["files"].pop(); pin["raw_root"] = str(tmp_path / "different-root")
    manifest.write_text(json.dumps(pin)); digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="outside"):
        load_manifest(manifest, digest)


def test_results_cannot_be_written_inside_a_git_repository(tmp_path):
    _, _, manifest, digest = native_fixture(tmp_path)
    repo = tmp_path / "public-repo"; repo.mkdir(); (repo / ".git").mkdir()
    with pytest.raises(ValueError, match="Git repository"):
        run_manifest(manifest, digest, repo / "real-output")
    assert not (repo / "real-output").exists()


def test_existing_output_is_never_overwritten(tmp_path):
    _, _, manifest, digest = native_fixture(tmp_path)
    out = tmp_path / "existing"; out.mkdir()
    with pytest.raises(ValueError, match="overwrite"):
        run_manifest(manifest, digest, out)


def test_raw_root_is_read_only_for_manifests_and_reports(tmp_path):
    _, policy, manifest, digest = native_fixture(tmp_path)
    root = Path(json.loads(manifest.read_text())["raw_root"])
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.parquet')}
    with pytest.raises(ValueError, match="raw root"):
        create_manifest(root, policy, root / "new-manifest.json")
    with pytest.raises(ValueError, match="raw root"):
        run_manifest(manifest, digest, root / "new-output")
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
