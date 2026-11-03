import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from market_microstructure.event_study_io import register_study, run_study
from market_microstructure.historical_io import run_manifest


def test_example_direct_execution_outside_repository(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTHONPATH=str(root/'src'),PYTHONDONTWRITEBYTECODE='1')
    output=tmp_path/'synthetic.html'
    process=subprocess.run([sys.executable,'-B',str(root/'examples/event_study_synthetic.py'),
                            '--output',str(output)],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert process.returncode==0,process.stderr
    assert 'SYNTHETIC_TEST_ONLY' in process.stdout
    assert '<svg' in output.read_text()


def study_fixture(tmp_path):
    pytest.importorskip('pyarrow')
    from test_historical_io import native_fixture
    _,_,manifest,digest=native_fixture(tmp_path)
    p2=tmp_path/'phase2';run_manifest(manifest,digest,p2)
    rules={'horizons_seconds':[60,300],'barrier_bps':10,'max_gap_ns':5*10**9,
           'bin_edges':[-1,-0.5,0,0.5,1]}
    protocol=tmp_path/'protocol.json'
    sha=register_study(p2,rules,protocol)
    return p2,protocol,sha


def test_static_report_and_repeat_reproduction(tmp_path):
    _,protocol,sha=study_fixture(tmp_path)
    a,b=tmp_path/'a',tmp_path/'b'
    result=run_study(protocol,sha,a)
    assert run_study(protocol,sha,b)==result
    for name in ('result.json','outcomes.csv','report.html','protocol.json'):
        assert (a/name).read_bytes()==(b/name).read_bytes()
    html=(a/'report.html').read_text()
    assert html.count('<svg')==4
    assert '<script' not in html and 'https://' not in html
    assert 'synthetic-long' not in html
    assert (a/'code/src/market_microstructure/event_study.py').exists()
    assert (a.stat().st_mode&0o777)==0o700


def test_protocol_modification_is_rejected(tmp_path):
    _,protocol,sha=study_fixture(tmp_path)
    protocol.write_text(protocol.read_text()+' ')
    with pytest.raises(ValueError,match='protocol SHA'):
        run_study(protocol,sha,tmp_path/'out')


def test_registered_protocol_bytes_are_preserved(tmp_path):
    _,protocol,_=study_fixture(tmp_path)
    # A valid externally pinned protocol need not use our JSON whitespace.
    protocol.write_text(json.dumps(json.loads(protocol.read_text()), separators=(',',':'))+'\n')
    sha=hashlib.sha256(protocol.read_bytes()).hexdigest()
    out=tmp_path/'out';run_study(protocol,sha,out)
    assert (out/'protocol.json').read_bytes()==protocol.read_bytes()
    assert hashlib.sha256((out/'protocol.json').read_bytes()).hexdigest()==sha


@pytest.mark.parametrize('name',['series.csv','report.json','input_manifest.json'])
def test_phase2_input_modification_is_rejected_before_outcomes(tmp_path,name):
    p2,protocol,sha=study_fixture(tmp_path)
    file=p2/name;file.write_bytes(file.read_bytes()+b' ')
    with pytest.raises(ValueError,match='Phase 2 artifact'):
        run_study(protocol,sha,tmp_path/'out')


def test_private_output_guard_and_no_overwrite(tmp_path):
    _,protocol,sha=study_fixture(tmp_path)
    repo=tmp_path/'repo';repo.mkdir();(repo/'.git').mkdir()
    with pytest.raises(ValueError,match='Git repository'):
        run_study(protocol,sha,repo/'actual')
    out=tmp_path/'existing';out.mkdir()
    with pytest.raises(ValueError,match='overwrite'):
        run_study(protocol,sha,out)
