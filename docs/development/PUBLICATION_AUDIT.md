# Publication readiness review

**Historical record:** the file inventory and verified counts below describe the
earlier release review. The later execution module and related files are outside
that inventory. Their current scope and verification are recorded in [PLAN.md](PLAN.md).

This is a source-release review, not an empirical research result or a guarantee
that every possible secret pattern can be detected. No commit, remote creation,
GitHub push, or package publication was performed.

## Release boundary

At this pre-commit audit, the repository has no commits, tracked/staged files, or remotes. The 29 files below
are the reviewed **candidates** for a future commit; they are not yet Git-tracked.

```text
.gitignore
LEARNING.md
PROVENANCE.md
PUBLICATION_AUDIT.md
README.md
pyproject.toml
examples/event_study_synthetic.py
examples/historical_synthetic.py
examples/liquidation_core.py
examples/research_walkthrough.py
examples/synthetic_walkthrough.py
src/market_microstructure/__init__.py
src/market_microstructure/asof.py
src/market_microstructure/event_study.py
src/market_microstructure/event_study_io.py
src/market_microstructure/gravity.py
src/market_microstructure/historical.py
src/market_microstructure/historical_io.py
src/market_microstructure/order_flow.py
src/market_microstructure/orderbook.py
tests/test_asof.py
tests/test_event_study.py
tests/test_event_study_io.py
tests/test_gravity.py
tests/test_historical.py
tests/test_historical_io.py
tests/test_order_flow.py
tests/test_orderbook.py
tests/test_research_walkthrough.py
```

Private inputs, account identifiers, native market/position files, real policies,
input inventories, private experimental scripts, real numeric results, and local
audit logs stay outside this source release. No actual dataset or numeric research
result was copied into the public repository.

The local environment, bytecode, editable-install metadata, caches and OS metadata
are ignored. They are not part of the reviewed release. Do not upload the whole
working directory, use force-add on ignored files, or include a local environment.

## Sensitive-content checks

Candidate filenames and contents were checked for symlinks, account addresses,
personal absolute paths, credential assignments, provider token formats, private-key
blocks, and known private-result identifiers. No candidate matched those checks.
There are no native datasets or credentials in the candidate list.

Ignore rules were checked for local environment files, credential filenames,
research data/report/result directories, Parquet/Arrow/databases and key files.
Ignore is a second guard, not authorization to put real data under this checkout.
The package finder is restricted to `market_microstructure*` under `src`, and
implicit package data inclusion is disabled.

## Verified behavior

- A separate virtual environment installed `.[dev,historical]` successfully.
- A second installation from a clean copy containing only the 29 reviewed source
  files also passed all 290 tests and all five demos without the original project.
- Python 3.13.15 / pytest 9.2.0 / PyArrow 25.0.1: **290 passed**, failures 0,
  errors 0, skipped 0. This includes four new synthetic integration/demo tests.
- `pip check` reported no broken requirements.
- All five example commands completed from outside the checkout and explicitly
  labeled their output `SYNTHETIC_TEST_ONLY`.
- The optional synthetic HTML report contained four inline SVG charts and the
  synthetic source label. CLI help for both optional local I/O modules executed.
- A built wheel contained only the nine package modules and four distribution
  metadata files. No dataset, key or personal-path pattern was found in it.
  Installing that wheel with no dependencies into a separate core-only environment
  ran all five examples successfully without PyArrow or pytest installed.
- The main synthetic demo connects as-of selection, all four microstructure metrics,
  Gravity/LGI, event-study censoring and counterfactual decomposition. Future account
  updates and late children are covered by focused tests.

These are software/synthetic checks, not evidence for market prediction or a causal
price contribution. Private historical tests are not included in the public count.
Declared Python >=3.11 compatibility is not a claim that every version was tested.

## Scope of changes

Changed existing files: `.gitignore`, `README.md`, `LEARNING.md`, `pyproject.toml`,
and the synthetic label in `examples/synthetic_walkthrough.py`.
Added files: `PROVENANCE.md`, this review, `examples/research_walkthrough.py`,
and `tests/test_research_walkthrough.py`.

The nine existing calculation/I/O modules and eight existing test files were retained.
Small per-module numeric validators are intentional; no cross-module refactor or
general historical counterfactual subsystem was introduced.
Original Liquidation Gravity source and prior private research outputs remain unchanged.

## Public-source readiness and remaining owner decision

**Public source: READY within the reviewed 29-file boundary. MIT-licensed release:
pending owner choice and attribution.** A LICENSE is not a GitHub requirement for
public visibility. Its absence is not a public-repository blocker; it means no
open-source license is being granted here. See [GitHub's policy](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).

The owner confirmed authority to publish and license the personal work. Neither the
original root nor this repository supplied a license to inherit. No LICENSE has been
added because the requested action was to review rights and propose choices.

Choose **MIT** (recommended) or **Apache-2.0**, provide the desired copyright-holder
attribution, and add matching license text/metadata before describing the project as
open-source licensed. Details and references are in [PROVENANCE.md](../../PROVENANCE.md).
No further financial research feature is required for this release boundary.

The reviewed scope identified no technical or sensitive-content blocker to that
public-source release. Residual risks are patterns the scan cannot detect, later
file additions, and private author/committer metadata in a future initial commit.
Inspect the public name/email (including an optional GitHub noreply address) before
creating that commit. Recheck the actual staged list against these 29 paths; do not
use an unconditional whole-directory add or include ignored files.
