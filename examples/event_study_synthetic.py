"""Invented paths only; not evidence about the actual BTC session."""

import argparse
from pprint import pprint

from market_microstructure.event_study import study_events
from market_microstructure.event_study_io import render_report
from market_microstructure.historical import replay_history
from market_microstructure.historical_io import _mkdir_private, _private_target


def synthetic_study_inputs():
    # Import the small account fixture only when this example is invoked.
    if __package__:
        from .historical_synthetic import synthetic_inputs
    else:
        from historical_synthetic import synthetic_inputs

    data, policy = synthetic_inputs()
    start = policy['start_ns']
    policy.update(end_ns=start+360*10**9, snapshot_age_ns=600*10**9)
    context, book = data['contexts'][0], data['bbo'][0]
    data['contexts'], data['bbo'] = [], []
    for i in range(361):
        now = start+i*10**9
        mid = '100.2' if 60 <= i < 120 else '99.8' if 180 <= i < 240 else '100'
        data['contexts'].append(dict(context,event_id=f'ctx-{i}',timestamp=now,ingested_at=now,mid_px=mid))
        data['bbo'].append(dict(book,event_id=f'book-{i}',timestamp=now,ingested_at=now))
    rules = {'horizons_seconds':[60,300],'barrier_bps':10,'max_gap_ns':5*10**9,
             'bin_edges':[-1,-0.5,0,0.5,1]}
    return data, policy, rules


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',help='Optional NEW report.html outside Git repositories')
    args=parser.parse_args()
    data,policy,rules=synthetic_study_inputs()
    features=replay_history(data,policy)['series']
    result=study_events(features,data['contexts'],data['bbo'],policy,rules)
    print('SYNTHETIC_TEST_ONLY — prices and positions are invented')
    pprint({h:{k:s[k] for k in ('valid_outcomes','censored','ambiguous','label_counts')}
            for h,s in result['horizons'].items()})
    if args.output:
        path=_private_target(args.output);_mkdir_private(path.parent)
        path.write_text(render_report(result,features,data['contexts'],{'registered_at_utc':'SYNTHETIC_DEMO_NOT_A_REGISTRATION'}),encoding='utf-8')
        path.chmod(0o600)


if __name__=='__main__':main()
