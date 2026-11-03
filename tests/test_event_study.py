from copy import deepcopy

import pytest

from examples.event_study_synthetic import synthetic_study_inputs
from market_microstructure.event_study import observed_outcome, study_events, validate_rules
from market_microstructure.historical import replay_history

S=10**9


def fixture():
    data,policy,rules=synthetic_study_inputs()
    policy.update(start_ns=0,end_ns=10*S,step_ns=S)
    rules.update(horizons_seconds=[2,4],max_gap_ns=2*S)
    row=dict(data['contexts'][0],timestamp=0,ingested_at=0)
    prices=[dict(row,event_id=f'p-{i}',timestamp=i*S,ingested_at=i*S,mid_px='100') for i in range(1,11)]
    return prices,policy,rules


def outcome(prices,policy,rules,**changes):
    return observed_outcome(prices,decision_ns=0,horizon_seconds=changes.pop('horizon_seconds',4),
                            reference_price=changes.pop('reference_price',100),policy=policy,rules=rules,**changes)


def test_hand_calculated_upper_first_and_terminal_return():
    prices,policy,rules=fixture()
    prices[0]['mid_px']='100.1';prices[1]['mid_px']='99.9';prices[3]['mid_px']='100.2'
    row=outcome(prices,policy,rules)
    assert row['label']==1 and row['first_upper_ns']==S and row['first_lower_ns']==2*S
    assert row['future_return_bps']==pytest.approx(20)
    assert row['terminal_received_ns']==4*S and row['terminal_age_ns']==0
    assert row['upper_hit'] and row['lower_hit']


@pytest.mark.parametrize('price,label',[('99.9',-1),('100.1',1),('100.05',0)])
def test_exact_barrier_boundary_and_neither(price,label):
    prices,policy,rules=fixture();prices[0]['mid_px']=price
    assert outcome(prices,policy,rules)['label']==label


def test_same_first_receipt_opposite_hits_is_ambiguous_not_order_dependent():
    prices,policy,rules=fixture();prices[0]['mid_px']='100.1'
    other=dict(prices[0],event_id='opposite',mid_px='99.9')
    for path in (prices+[other],list(reversed(prices+[other]))):
        row=outcome(path,policy,rules)
        assert row['label'] is None and row['label_status']=='ambiguous'
        assert row['future_return_bps'] is None


def test_conflicting_terminal_price_cannot_be_chosen_by_id_order():
    prices,policy,rules=fixture();prices.append(dict(prices[3],event_id='conflict',mid_px='100.01'))
    assert outcome(prices,policy,rules)['reason']=='conflicting_terminal_prices'


def test_full_path_required_even_after_an_early_hit():
    prices,policy,rules=fixture();prices[0]['mid_px']='101'
    row=outcome([prices[0],prices[3]],policy,rules)
    assert row['reason']=='price_gap' and row['label'] is None


def test_observation_end_is_censored_even_if_early_prices_hit():
    prices,policy,rules=fixture();policy['end_ns']=3*S;prices[0]['mid_px']='101'
    row=outcome(prices,policy,rules)
    assert row['reason']=='observation_end' and row['label'] is None


def test_no_future_price_and_end_staleness():
    prices,policy,rules=fixture()
    assert outcome([],policy,rules)['reason']=='no_future_price'
    assert outcome(prices[:1],policy,rules)['reason']=='stale_terminal_price'
    row=outcome(prices[:2],policy,rules)
    assert row['label']==0 and row['terminal_age_ns']==2*S


def test_decision_time_and_post_horizon_observations_cannot_change_label():
    prices,policy,rules=fixture();baseline=outcome(prices,policy,rules)
    extra=[dict(prices[0],event_id='at-decision',timestamp=0,ingested_at=0,mid_px='101'),
           dict(prices[0],event_id='after-end',timestamp=4*S+1,ingested_at=4*S+1,mid_px='99')]
    assert outcome(prices+extra,policy,rules)==baseline


@pytest.mark.parametrize('changes,reason',[
    ({'session_id':'other'},'session_changed'),
    ({'mid_px':None},'invalid_future_mid'),({'mid_px':'NaN'},'invalid_future_mid'),
    ({'mid_px':0},'invalid_future_mid'),
    ({'timestamp':S+1,'timestamp_basis':'exchange'},'price_clock_inversion'),
    ({'timestamp':0,'timestamp_basis':'exchange'},'predecision_event_received_late'),
    ({'timestamp_basis':'unknown'},'unknown_price_time_basis'),
    ({'source_kind':'REAL_OBSERVED'},'price_provenance_mismatch'),
])
def test_bad_future_observations_are_explicitly_censored(changes,reason):
    prices,policy,rules=fixture();prices[0].update(changes)
    assert outcome(prices,policy,rules)['reason']==reason


def test_stale_exchange_price_cannot_be_used_as_new_future_price():
    prices,policy,rules=fixture()
    prices[3].update(timestamp=1,timestamp_basis='exchange')
    assert outcome(prices,policy,rules)['reason']=='stale_future_price'


def test_duplicate_future_event_is_not_silently_counted_twice():
    prices,policy,rules=fixture();prices.append(dict(prices[0]))
    assert outcome(prices,policy,rules)['reason']=='duplicate_price_event'


@pytest.mark.parametrize('change',[{'barrier_bps':0},{'barrier_bps':10000},
    {'max_gap_ns':0},{'horizons_seconds':[60,60]},{'horizons_seconds':[True]},
    {'bin_edges':[-1,0,1]}])
def test_invalid_registered_rules(change):
    _,_,rules=fixture();rules.update(change)
    with pytest.raises(ValueError):validate_rules(rules)


def test_same_outcomes_cohort_for_lgi_and_aligned_obi():
    data,policy,rules=synthetic_study_inputs();features=replay_history(data,policy)['series']
    before=deepcopy(features)
    result=study_events(features,data['contexts'],data['bbo'],policy,rules)
    for h,s in result['horizons'].items():
        assert s['common_valid_outcomes']==s['valid_outcomes']
        assert sum(b['valid_outcomes'] for b in s['obi_bins_common'])==s['common_valid_outcomes']
        assert sum(b['valid_outcomes'] for b in s['lgi_bins_common'])==s['common_valid_outcomes']
        assert sum(s['label_counts'].values())==s['valid_outcomes']
        assert s['candidate_anchors']==s['valid_outcomes']+s['censored']+s['ambiguous']
    assert features==before


def test_obi_comparison_is_disabled_for_misaligned_phase2_book():
    data,policy,rules=synthetic_study_inputs();features=replay_history(data,policy)['series']
    features[0]['bbo_received_ns']-=1
    result=study_events(features,data['contexts'],data['bbo'],policy,rules)
    assert result['outcomes'][0]['obi'] is None
    assert result['outcomes'][0]['obi_exclusion_reason']=='phase2_bbo_identity_mismatch'


def test_future_feature_information_cannot_enter_lgi_bins():
    data,policy,rules=synthetic_study_inputs();features=replay_history(data,policy)['series']
    features[0]['feature_max_received_ns']=features[0]['decision_ns']+1
    row=study_events(features,data['contexts'],data['bbo'],policy,rules)['outcomes'][0]
    assert row['reason']=='feature_not_available_at_decision' and row['label'] is None


def test_observation_tail_and_nonoverlap_anchor_selection_are_ex_ante():
    data,policy,rules=synthetic_study_inputs();features=replay_history(data,policy)['series']
    result=study_events(features,data['contexts'],data['bbo'],policy,rules)
    assert result['horizons']['60']['valid_outcomes']==11
    assert result['horizons']['60']['censored']==2
    assert result['horizons']['300']['valid_outcomes']==3
    assert result['horizons']['300']['censored']==10
    assert result['horizons']['60']['nonoverlap_sensitivity']['valid_outcomes']==6
    assert result['iid_inference_performed'] is False


def test_mutating_later_prices_cannot_change_an_earlier_horizon():
    data,policy,rules=synthetic_study_inputs();features=replay_history(data,policy)['series']
    baseline=study_events(features,data['contexts'],data['bbo'],policy,rules)['outcomes'][0]
    for row in data['contexts']:
        if row['ingested_at']>policy['start_ns']+60*S:row['mid_px']='110'
    assert study_events(features,data['contexts'],data['bbo'],policy,rules)['outcomes'][0]==baseline
