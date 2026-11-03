"""SYNTHETIC pipeline only: as-of -> LGI -> observed-price study -> repricing."""

import math
from pprint import pprint

from market_microstructure import (
    bid_ask_spread, gravity, liquidation_imbalance, notional_depth,
    order_book_imbalance, prepare_exposure, select_asof, trade_imbalance,
)
from market_microstructure.event_study import study_events
from market_microstructure.historical import replay_history


def synthetic_inputs():
    if __package__:
        from .event_study_synthetic import synthetic_study_inputs
    else:
        from event_study_synthetic import synthetic_study_inputs
    data, policy, rules = synthetic_study_inputs()
    # Update one invented account at +60 seconds, including all its child rows.
    old = data['accounts'][0]
    received = policy['start_ns'] + 60 * 10**9
    header = dict(old, snapshot_id='synthetic-update', timestamp=received,
                  ingested_at=received, requested_at=received - 10**9)
    children = [dict(p, snapshot_id=header['snapshot_id'], timestamp=received,
                     ingested_at=received,
                     signed_size='3' if p['coin'] == 'BTC' else p['signed_size'])
                for p in data['positions'] if p['snapshot_id'] == old['snapshot_id']]
    data['accounts'].append(header)
    data['positions'].extend(children)
    return data, policy, rules


def positions_at(data, policy, decision):
    """Select the two complete invented accounts; not a general native-data loader.

    Completeness below is a fixture assertion. replay_history independently checks
    the native-shaped headers and all-asset child rows in the same demo.
    """
    positions = []
    known = {h['wallet'] for h in data['accounts'] if h['ingested_at'] <= decision}
    for wallet in sorted(known):
        records = []
        for header in data['accounts']:
            if header['wallet'] != wallet:
                continue
            children = [p for p in data['positions']
                        if p['snapshot_id'] == header['snapshot_id']
                        and p['ingested_at'] <= decision]
            records.append(dict(header, record_id=header['snapshot_id'], entity=wallet,
                                complete=len(children) == header['scoped_position_count'],
                                positions=[(p['signed_size'], p['liquidation_px'])
                                           for p in children if p['coin'] == 'BTC']))
        selected = select_asof(records, decision_ns=decision,
                               max_age_ns=policy['snapshot_age_ns'],
                               source_kind=policy['source_kind'], source=policy['source'],
                               entity=wallet)
        if selected['status'] != 'available':
            raise ValueError('This synthetic demo requires complete fresh accounts')
        positions.extend(selected['record']['positions'])
    return positions


def reprice(positions, price, policy):
    """Use existing public calculation functions on a frozen synthetic payload."""
    pairs = {'upper': [], 'lower': []}
    for size, threshold in positions:
        prepared = prepare_exposure(size, threshold, price)
        if prepared['status'] == 'included':
            pairs[prepared['side']].append((prepared['notional'], prepared['distance_bps']))
    if positions and not any(pairs.values()):
        raise ValueError('No valid synthetic liquidation threshold')
    up = gravity(pairs['upper'], policy['tau_bps'])
    down = gravity(pairs['lower'], policy['tau_bps'])
    return liquidation_imbalance(up, down, policy['epsilon'])


def run_demo():
    data, policy, rules = synthetic_inputs()
    features = replay_history(data, policy)['series']
    study = study_events(features, data['contexts'], data['bbo'], policy, rules)
    baseline, current = features[1], features[2]  # +30s -> +60s, chosen before outcomes.
    before = positions_at(data, policy, baseline['decision_ns'])
    after = positions_at(data, policy, current['decision_ns'])
    b = reprice(before, baseline['reference_mid'], policy)
    a = reprice(after, current['reference_mid'], policy)
    po = reprice(before, current['reference_mid'], policy)
    so = reprice(after, baseline['reference_mid'], policy)
    # The small float helper and historical Decimal preparation agree to tolerance.
    assert math.isclose(b, baseline['lgi'], abs_tol=1e-12)
    assert math.isclose(a, current['lgi'], abs_tol=1e-12)
    ep, es = po - b, so - b
    interaction = math.fsum((a, -po, -so, b))
    identity_error = a - b - math.fsum((ep, es, interaction))
    assert abs(identity_error) <= 1e-12
    book = data['bbo'][0]
    decision = current['decision_ns']
    trades = [{'timestamp': decision - 2, 'ingested_at': decision - 1,
               'price': 100, 'size': 3, 'side': 'buy'},
              {'timestamp': decision - 2, 'ingested_at': decision - 1,
               'price': 100, 'size': 1, 'side': 'sell'}]
    return {'source_kind': 'SYNTHETIC_TEST_ONLY',
            'microstructure': {
                'spread': bid_ask_spread(book['bid_px'], book['ask_px']),
                'obi': order_book_imbalance(book['bid_sz'], book['ask_sz']),
                'depth': notional_depth(book['bid_px'], book['ask_px'], book['bid_sz'], book['ask_sz']),
                'trade_flow': trade_imbalance(trades, decision_ns=decision, window_ns=30*10**9)},
            'eligible_feature_points': sum(r['status'] == 'available' for r in features),
            'event_study': {h: {k: s[k] for k in ('candidate_anchors','valid_outcomes','censored','label_counts')}
                            for h, s in study['horizons'].items()},
            'counterfactual': {'baseline': b, 'actual': a, 'price_only': po, 'state_only': so,
                               'actual_change': a-b, 'price_effect': ep, 'state_effect': es,
                               'interaction': interaction, 'identity_error': identity_error}}


if __name__ == '__main__':
    print('SYNTHETIC_TEST_ONLY — invented accounts, prices and outcomes; no market evidence')
    pprint(run_demo(), sort_dicts=False)
