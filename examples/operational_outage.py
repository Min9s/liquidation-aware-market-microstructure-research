"""One deterministic quote-outage, client-kill and reconciliation scenario."""
from durable_order_recovery import run_crash_case, _print_phase


def main():
    result = run_crash_case(operational=True)
    print('SYNTHETIC_TEST_ONLY — manual fills, zero fees, POSIX client SIGKILL')
    print('Logical decision clock: quote timestamp=0; TTL=10 ns; no wall-clock market assumptions')
    _print_phase('t=10 age==TTL: accepted, committed before send', result['committed'])
    _print_phase('unknown: absent query retains reservation', result['unknown'])
    _print_phase('partial: query applies 3@99', result['partial'])
    for outage in result['outages']:
        _print_phase(f"t=11 {outage['label']}: {outage['reason']}, new exposure rejected", outage['recovery'])
    for label, reason in [('before_crash', 'cancel pending; 2@100 and cancel response withheld'),
                          ('restart', 'SIGKILL; committed state identical'),
                          ('final', 'terminal query then duplicate/reversed reports'),
                          ('recovered', 'independent venue ledger and persisted execution IDs match')]:
        _print_phase(f'{label}: {reason}', result[label])
    print('Independent arithmetic: 1000 - 3*99 - 2*100 = 503; inventory=5; reservations=0')


if __name__ == '__main__':
    main()
