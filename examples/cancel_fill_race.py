"""Deterministic 4+3 fill/cancel race; reopen and reverse delivery audit."""
from pathlib import Path
from tempfile import TemporaryDirectory

from durable_order_recovery import (_inputs, _require, _execution_facts,
                                    _venue_ledger, _assert_ledger, _print_phase)
from market_microstructure.funded_journal import FundedJournal
from market_microstructure.simulated_venue import SimulatedVenue


def main():
    instruments, policy, marks, order = _inputs()
    venue = SimulatedVenue(instruments)
    print('SYNTHETIC_TEST_ONLY — manual 4@99 + 3@100, zero fees; reopen is not a process kill')
    with TemporaryDirectory(prefix='cancel-fill-race-') as directory:
        path = Path(directory) / 'race.sqlite'
        with FundedJournal.create(path, instruments, policy) as store:
            ack = venue.submit(store.submit('submit', order, now_ns=0, marks=marks)['outgoing'])
            first = venue.execute('buy-a', '4', '99')
            store.apply('four', first)
            _print_phase('4 filled', store.recover())
            store.request_cancel('cancel', 'buy-a')
            second = venue.execute('buy-a', '3', '100')
            terminal = venue.cancel('buy-a')
            before = store.recover()
            _print_phase('cancel pending; additional fills and cancel response lost', before)
        with FundedJournal.open(path) as store:
            _require(store.recover() == before, 'Restart changed committed state')
            store.reconcile('query', 'buy-a', venue.query('buy-a'))
            expected = store.recover()['snapshot']
            for index, report in enumerate((terminal, second, first, ack)):
                store.apply(f'late-{index}', report)
                store.apply(f'duplicate-{index}', report)
                _require(store.recover()['snapshot'] == expected, 'Late report changed terminal facts')
            final = store.recover()
        oracle = _venue_ledger(instruments, policy['initial_cash'], _execution_facts((first, second)))
        _assert_ledger(final, oracle)
        record = final['snapshot']['lifecycle']['orders']['buy-a']
        _require((record['status'], record['filled_quantity'], record['remaining_quantity']) ==
                 ('canceled', '7', '3'), 'Wrong final order state')
        with FundedJournal.open(path) as store:
            _require(store.recover() == final, 'Final restart differs')
        _print_phase('final: independent venue ledger matches', final)
        print('Filled=7; canceled remainder=3; cash=1000-4*99-3*100=304; reserved=0')


if __name__ == '__main__':
    main()
