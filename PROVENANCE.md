# Provenance and license review

## Source of the research definitions

The original Liquidation Gravity project is a separate private research system.
Its source paths below identify the definitions consulted, not files distributed here.
No runtime dependency on that project is required.

| Public component | Original research reference | Work in this repository |
|---|---|---|
| `orderbook.py`, `order_flow.py` | `microstructure/orderbook.py`, `microstructure/order_flow.py` | Small functions, explicit buy/sell contract, input validation, overflow and empty-observation handling |
| `gravity.py` | `liquidation/gravity.py`: `gravity`, `imbalance` | Same exponential sum and epsilon LGI; independent numeric validation and one-position preparation |
| `asof.py` | `research/inputs.py`: receipt-time timelines and latest observations | Explicit source/entity contract, ties, clock, completeness and stale rejection; no fallback to older state |
| `historical.py`, `historical_io.py` | `liquidation/surface.py`: threshold-side rules and Decimal boundaries; `research/inputs.py`: frozen inventories | Narrow BTC replay, header/child reconciliation, availability lineage, private output guards and before/after hashes |
| `event_study.py`, `event_study_io.py` | `models/first_passage.py`: complete sampled path and unresolved ties | Provenance-aware price checks, Decimal barriers, fixed cohorts, aligned OBI comparison and static reporting |
| `examples/research_walkthrough.py` | Same repricing identity considered in private research | Newly authored synthetic fixture update and integration example using public functions; no historical audit scripts or results copied |
| `execution.py`, `execution_contracts.py`, `execution_risk.py` and execution examples/tests | Original `backtest/contracts.py`, `costs.py`, `engine.py`, `metrics.py`, `fixtures.py` and execution tests | Newly implemented bounded adaptation of available-at-arrival selection, IOC depth matching, validate-before-commit, shared collateral, Decimal cash accounting and per-symbol reconciliation; explicit before/projected/after risk diagnostics; no imports from the private project, funding or real-data admission |
| `execution_ledger.py`, `execution_journal.py`, `examples/journal_recovery.py` and journal tests | This repository's execution ledger and Python standard-library SQLite | Extracted fill accounting and newly implemented frozen synthetic event journal, transactional deduplication and full-prefix account reconstruction; not an original private collector/checkpoint implementation or a live exchange OMS |
| `execution_monitor.py`, `examples/risk_monitoring.py` and monitoring tests | This repository's execution replay, fill ledger and portfolio valuation | New passive observations and risk episodes over available quotes, fills and current-quote expiries; no matching changes, automatic liquidation or live monitoring service |
| `order_protocol.py`, `order_lifecycle.py`, `simulated_venue.py` and lifecycle examples/tests | Newly authored custom synthetic protocol; shared Decimal input bounds from this repository | In-memory order/cancel state, explicitly supplied fills, cumulative-report reconciliation, duplicate/stale delivery checks and log replay; not copied from a venue protocol, FIX engine or private OMS; not yet integrated with collateral or SQLite |
| `order_reservations.py`, `funded_orders.py` and funded lifecycle examples/tests | This repository's synthetic lifecycle, Decimal contracts and existing `quote_problem`/`exposure_checks` functions | New cash-funded buy and inventory-backed sell admission, pending/unknown reservations, committed-exposure caps and self-contained in-memory replay; zero fees, no borrowing, no derivative margin accounting or durable journal |

This is a reimplementation/adaptation of bounded calculations and conventions,
not a claim to have invented spread, imbalance, exponential weighting, or first passage.
The original system's collectors, registry, raw payloads, configuration, storage,
Surface implementation, models, and reports are not vendored.

## Public and private evidence

All data supplied by the public examples and tests are invented. Even a test using
the `REAL_OBSERVED` string tests a declaration/validation path using synthetic values;
the string is not a source certificate.

Private research used local observed data. Its datasets, account identifiers,
manifests, policies, code snapshots, experiment scripts and numeric results remain
outside this repository. No public demo number should be presented as an actual
market finding. A synthetic identity check does not reproduce a private empirical result.

## AI-assisted development

AI assistance covered architecture discussion, implementation, synthetic tests,
documentation and review. The maintainer supplied goals, scope decisions and review.
Automated checks establish only the properties they actually test. The maintainer
should be able to explain input contracts, boundary rules, numerical identities,
and limitations before claiming ownership of engineering decisions in an interview.

## Rights and current license status

The maintainer explicitly stated that the original and current work are personal
work and that they hold the authority to publish and license it, including the
absence of conflicting coauthor/employer/institution rights. This records that
statement; it is not an independent chain-of-title investigation.

The inspected original project had no root LICENSE/COPYING/NOTICE file and no
license declaration in its project metadata. Its local Git repository had no
commits from which to establish a public upstream license. No third-party license
was inferred from the availability of local source.

The owner requested license options after the rights review. **No LICENSE file or
license identifier has been added, and no license is claimed in package metadata.**
The owner's preferred copyright-holder attribution has not been supplied and is
not inferred from a filesystem username, Git account, or AI tooling.

## External dependencies

The calculation core has no third-party runtime requirements. Installed package
metadata and shipped license/notice files were reviewed for the following tools.
They are installed separately, not vendored or included in this project's wheel.

| Component | Role | Declared license / upstream |
|---|---|---|
| pytest | Optional tests | MIT; [pytest](https://github.com/pytest-dev/pytest), shipped `LICENSE` |
| PyArrow | Optional native Parquet I/O | Apache-2.0; [Apache Arrow](https://github.com/apache/arrow), shipped `LICENSE.txt` and `NOTICE.txt` with third-party notices |
| setuptools | Build backend | MIT; [setuptools](https://github.com/pypa/setuptools), shipped `LICENSE` |
| iniconfig, pluggy | pytest dependencies in the audited environment | MIT; [iniconfig](https://github.com/pytest-dev/iniconfig), [pluggy](https://github.com/pytest-dev/pluggy) |
| packaging | pytest dependency in the audited environment | Apache-2.0 OR BSD-2-Clause; [packaging](https://github.com/pypa/packaging) |
| Pygments | pytest dependency in the audited environment | BSD-2-Clause; [Pygments](https://github.com/pygments/pygments) |
| pip | Installation tool, not a project dependency | MIT for pip itself; its separately shipped vendored notices remain applicable; [pip](https://github.com/pypa/pip) |

A future MIT license would cover only rights the owner can grant in this project;
it would not relicense these dependencies or their bundled components. If any
dependency source/binary is later redistributed, review its own license and notices
for that artifact rather than assuming this source-only review covers bundling.

No incompatible third-party source was identified in the reviewed public files.
No public Python file was a byte-identical copy of a whole original source file;
this comparison does not establish the provenance of every snippet. The local
original has no commit history or inherited license to independently resolve that
question. The maintainer's rights statement remains the basis for the reused work.

## Public visibility versus open-source licensing

GitHub does not require an owner to choose a LICENSE for a public repository.
Without one, public access and GitHub's view/fork permissions do not amount to a
general MIT-style grant to use, modify and redistribute the work. With MIT, those
permissions are granted subject to preserving its copyright/license notice.
The absence of LICENSE is not by itself a blocker to a public, unlicensed source
portfolio. It is a blocker to accurately claiming this project is MIT-licensed.

## Optional license choice

| Option | Reason to choose it | Reference |
|---|---|---|
| MIT — recommended | Short permissive terms; retain the copyright and license notice | [MIT](https://choosealicense.com/licenses/mit/) |
| Apache-2.0 | Permissive terms with an explicit contributor patent grant and change-notice requirements | [Apache-2.0](https://choosealicense.com/licenses/apache-2.0/) |

Before presenting this as open-source licensed software, choose one, add its
unaltered text with the owner's chosen attribution, and make package metadata and
README consistent. This does not license or disclose the private datasets.
See [GitHub's licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).
