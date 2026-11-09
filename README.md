# Order Management System Simulator

Python-based order management simulator covering order lifecycle, cash reservation, execution reports, cancellation, and recovery after interrupted operations. It uses Decimal accounting and a SQLite journal, with a simulated exchange and a limited set of FIX 4.4 business fields.

## Features

- Validate orders and reserve cash for buys or inventory for sells.
- Apply partial fills and terminal reports to order state and account balances together.
- Detect duplicate or conflicting reports without booking the same fill twice.
- Keep reservations during an unconfirmed cancellation; accept fills that arrive after the cancel request.
- Restore committed state after restart and reconcile it with supplied exchange reports.
- Gate new buys in recovery mode until fresh status evidence and unresolved incidents have been checked.

The account is single-currency, fully funded, and fee-free, with no borrowing or short selling. Fills are specified by the examples; they are not inferred from market data.

## System design

[`TradingSession`](src/market_microstructure/trading_session.py) owns the supported execution path and a cooperative process lock. It enters `RECOVERY` on startup and delegates to the FIX adapter, operational controls, and funded order journal.

1. Validate a command and its effect on orders, balances, and reservations.
2. Commit the command, result, and snapshot in one SQLite transaction.
3. Return an outgoing request for the caller to dispatch to the simulated exchange.
4. Validate incoming reports and commit the resulting order and account state together.

A repeated command returns its recorded result with `outgoing=None`. Persistence precedes dispatch, but does not guarantee delivery. Exchange queries and report redelivery are supplied by the caller.

## Recovery example

The [FIX recovery demo](examples/fix_order_recovery.py) submits a buy for 10 units at a limit of 100. Four fill at 99 before cancellation is requested. The exchange fills another three at 100 and cancels the remaining three, but those reports are withheld before the client process is killed with `SIGKILL`.

| Account | Restarted | Reconciled |
|---|---:|---:|
| Cash | 604 | 304 |
| Reserved cash | 600 | 0 |
| Inventory | 4 | 7 |

Restart restores only committed facts. A status query cannot recover the missing trade IDs by itself. The demo manually redelivers the archived reports, including duplicates, then supplies a fresh status query before resuming `NORMAL` operation.

The final order is `canceled`, with seven units filled and three canceled. An independent Decimal ledger checks `1,000 − 4×99 − 3×100 = 304`. These are synthetic accounting amounts, with zero fees.

## Testing

The tests cover duplicate and out-of-order reports, cancel/fill races, conflicting execution identities, concurrent reservations, transaction rollback, and interruption before and after commit. Recovery cases include actual client `SIGKILL` and 24 report orderings across five restart boundaries.

Accounting checks require available cash and inventory to remain nonnegative, unresolved orders to retain their reservations, and replayed snapshots to agree with the journal. Independent ledgers check the recovery examples against the simulated exchange's execution facts.

[Current verification and performance evidence](docs/OPERATIONAL_EVIDENCE.md) · [Historical engineering audit](docs/ENGINEERING_AUDIT.md) · [CI](https://github.com/Min9s/order-management-system-simulator/actions/workflows/ci.yml)

## Run locally

Python 3.11+ on macOS or Linux. No API keys or market data are required.

```bash
git clone https://github.com/Min9s/order-management-system-simulator.git
cd order-management-system-simulator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,historical]'
python examples/fix_order_recovery.py
python scripts/verify_project.py
```

The verifier runs the tests, all public examples, local documentation link checks, and dependency checks. For tests alone, run `python -m pytest -q`. The recovery demo kills only its own child process and cleans up its temporary database.

## Technical details and scope

- [Order lifecycle](docs/ORDER_LIFECYCLE.md) and [funding rules](docs/ORDER_FUNDING.md)
- [Journal transactions and restart behavior](docs/DURABLE_ORDERS.md)
- [Recovery modes, incidents, and reconciliation](docs/OMS_OPERATIONS.md)
- [Supported FIX business fields](docs/FIX_ADAPTER.md)
- [Earlier research and learning notes](docs/LEARNING.md) · [Provenance and AI assistance](PROVENANCE.md)

This is a simulator, not a production OMS. The FIX adapter handles field objects for new orders, execution reports, cancellation, limited cancel rejects, and explicit status queries. It has no FIX wire/session layer, Resend/Gap Fill, socket reconnection, or automatic retransmission.

Power-loss durability, distributed fencing, Windows, and NFS have not been validated. Direct low-level journal calls bypass session ownership protection. Full-history validation grows with journal history, and databases with changed code hashes are rejected rather than migrated. No separate license has been assigned.
