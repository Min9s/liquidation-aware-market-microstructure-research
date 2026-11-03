import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from examples.research_walkthrough import positions_at, run_demo, synthetic_inputs


def test_synthetic_pipeline_and_independent_counterfactual_arithmetic():
    result = run_demo()
    assert result['source_kind'] == 'SYNTHETIC_TEST_ONLY'
    assert result['microstructure']['spread']['bps'] == 200
    assert result['microstructure']['obi'] == 0.5
    assert result['microstructure']['depth']['total_notional'] == 398
    assert result['microstructure']['trade_flow']['imbalance'] == 0.5
    assert result['eligible_feature_points'] == 13
    assert result['event_study']['60']['valid_outcomes'] == 11
    assert result['event_study']['60']['censored'] == 2
    assert result['event_study']['300']['valid_outcomes'] == 3
    counter = result['counterfactual']
    assert counter['baseline'] == pytest.approx(-0.49846941813201256, abs=1e-12)
    # Equal long/short sizes simplify LGI to tanh((d_down-d_up)/(2*tau)).
    assert counter['actual'] == pytest.approx(math.tanh(125/100.2), abs=1e-12)
    assert counter['state_only'] == pytest.approx(math.tanh(-0.75), abs=1e-12)
    assert counter['price_only'] == pytest.approx(math.tanh(125/100.2+math.log(1.5)/2), abs=1e-12)
    assert abs(counter['identity_error']) <= 1e-12


def test_future_update_is_not_applied_to_baseline():
    data, policy, _ = synthetic_inputs()
    start = policy['start_ns']
    assert positions_at(data, policy, start+30*10**9) == [('2','99.9'),('-3','100.25')]
    assert positions_at(data, policy, start+60*10**9) == [('3','99.9'),('-3','100.25')]


def test_late_child_cannot_complete_current_header_or_revive_old_state():
    data, policy, _ = synthetic_inputs()
    decision = policy['start_ns']+60*10**9
    for row in data['positions']:
        if row['snapshot_id'] == 'synthetic-update' and row['coin'] == 'BTC':
            row['ingested_at'] = decision+1
    with pytest.raises(ValueError, match='complete fresh accounts'):
        positions_at(data, policy, decision)


def test_demo_direct_execution_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root/'src'), PYTHONDONTWRITEBYTECODE='1')
    run = subprocess.run([sys.executable, '-B', str(root/'examples/research_walkthrough.py')],
                         cwd=tmp_path, env=env, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert 'SYNTHETIC_TEST_ONLY' in run.stdout
    assert 'counterfactual' in run.stdout and 'event_study' in run.stdout
