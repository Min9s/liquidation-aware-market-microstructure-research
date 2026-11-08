from examples.durable_order_recovery import run_crash_case


def test_quote_outage_blocks_exposure_but_allows_cleanup_and_crash_recovery():
    result = run_crash_case(operational=True)
    assert [r['label'] for r in result['outages']] == ['stale_quote', 'missing_quote']
    for row in result['outages']:
        assert row['reason'] == 'portfolio_mark_unavailable'
        assert row['recovery']['snapshot']['account'] == result['partial']['snapshot']['account']
    assert result['committed']['snapshot']['account']['reserved_cash'] == '1000'
    assert result['unknown']['snapshot']['account']['reserved_cash'] == '1000'
    assert result['restart'] == result['before_crash']
    assert result['oracle'] == dict(cash='503', inventory={'SYNTH-A': '5'}, executions=2)
    assert result['redelivery_unchanged'] and result['execution_ids_match']
    assert result['final'] == result['recovered']
    assert not result['final']['reconciliation']


def test_quote_failure_reason_is_not_masked_by_insufficient_cash(tmp_path):
    from market_microstructure.funded_journal import FundedJournal
    from test_funded_journal import INSTRUMENTS, POLICY, MARKS, ORDER
    # Identical affordable order: TTL itself succeeds; TTL+1 and missing fail.
    for name, now, marks, accepted in [('boundary', 10, MARKS, True),
                                        ('stale', 11, MARKS, False),
                                        ('missing', 11, {}, False)]:
        path = tmp_path / f'{name}.sqlite'
        with FundedJournal.create(path, INSTRUMENTS, POLICY) as store:
            before = store.recover()['snapshot']
            decision = store.submit('probe', dict(ORDER, quantity='1', limit_price='1'),
                                    now_ns=now, marks=marks)
            assert decision['result']['accepted'] is accepted
            if not accepted:
                assert decision['result']['reason'] == 'portfolio_mark_unavailable'
                assert decision['outgoing'] is None
                assert store.recover()['snapshot'] == before
        with FundedJournal.open(path) as store:
            assert store.submit('probe', dict(ORDER, quantity='1', limit_price='1'),
                                now_ns=now, marks=marks)['outgoing'] is None
