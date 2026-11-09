# 운영 장애와 저장 비용 검증

Current publication summary and reproduction commands appear first. The later development-stage records retain their original counts, measurements and scope; they are historical evidence, not current-main results.

## Current verified baseline

`541cbc34932498a7ca97e2ad94f681786e4fcd47` includes [FIX revision admission hardening](FIX_ADAPTER.md) and [PR #4](https://github.com/Min9s/order-management-system-simulator/pull/4). The [hosted main run](https://github.com/Min9s/order-management-system-simulator/actions/runs/42603496103) passed 682 tests, zero skipped, 19 examples, 106 local documentation links and dependency checks on both Python 3.11 and 3.13. The 106 links describe that baseline, not a fixed count for future documentation. Local macOS 3.11.6/3.13.15 verification also passed. Existing tests include actual SIGKILL, before/after-commit recovery, accounting, cancellation races, tamper rejection and code-pinned database refusal. These are synthetic regression checks, not power-loss, FIX-session or live-venue validation.

## Immutable-history A/B measurements

Baseline A: `7ac443302ed6b5c29114c7ebf4522c05ee0c79af`; candidate B: `51014c92aee677c7e9c219524cf62e95c165907f`. B is now merged. Only private funded history representation changed: immutable JSON strings can be shared by deepcopy; mutable working state and history lists remain isolated. Exports decode detached dictionaries. Full replay/result/snapshot checks and SQLite DELETE/FULL durability settings remain active. No schema or migration was added; existing code-definition fingerprints still reject different implementations.

The [published timing projection](../benchmarks/immutable-funded-history.csv) contains 1,792 normal observations from each of the original and revalidation experiments. [Metadata, source hashes, summaries and diagnostic projections](../benchmarks/immutable-funded-history.json) define the selected columns and SHA-256 of each externally preserved full JSON. Projections omit snapshots, process IDs, local paths and most function traces; they are not full raw archives. All retained measurement values are copied exactly. The six older benchmark JSONs remain unchanged.

Each source version generated its own synthetic fixtures via APIs. At command history H=10/50/150/500, live state stays one buy10@100 with fill1@99, cash999901, inventory1 and reserved900; padding is committed policy-denied submissions. Native means FundedJournal, Operational/FIX use TradingSession with startup/reconciliation before the steady-command timer. Submit, policy rejection, invalid rollback, fill, reconciliation, recover, validation, reopen and operational/FIX startup are covered. This is fixed-live-state history growth, not growing active populations.

Environment: CPython3.13.15, macOS26.6.2 arm64, Apple M4 Max14 cores/36GiB, APFS, SQLite3.53.4; DELETE journal, synchronous FULL, fullfsync0 unchanged. One discarded warmup pair per cell; 21 submit pairs and seven pairs for other operations. A/B and scenario order randomized/interleaved with seed314159. Every observation uses a fresh subprocess and database copy; setup/import/extra equivalence checks are excluded, actual command replay/validation/commit included. Three paired diagnostic repetitions per mode/history run separately. Logical results, full recovered snapshots and exported-history hashes match; only random session owner ID/PID are normalized.

Revalidation submit latency below is uninstrumented wall time in milliseconds. Δ=B−A; positive is slower. Empirical p95 is descriptive, not a tail guarantee.

|Path|H|Baseline p50 / p95 ms|Candidate p50 / p95 ms|p50 Δms (%)|
|---|---:|---:|---:|---:|
|native|10|1.593 / 1.686|1.617 / 1.680|+0.024 (+1.50%)|
|native|50|5.151 / 5.202|5.221 / 5.338|+0.070 (+1.37%)|
|native|150|13.983 / 14.198|14.234 / 14.378|+0.251 (+1.79%)|
|native|500|44.900 / 45.994|45.780 / 46.307|+0.880 (+1.96%)|
|operational|10|1.786 / 1.821|1.661 / 1.708|-0.125 (-7.01%)|
|operational|50|10.420 / 10.549|6.674 / 6.839|-3.746 (-35.95%)|
|operational|150|53.681 / 54.001|19.861 / 20.258|-33.820 (-63.00%)|
|operational|500|435.817 / 439.315|73.561 / 74.547|-362.255 (-83.12%)|
|fix|10|2.229 / 2.264|2.112 / 2.161|-0.118 (-5.27%)|
|fix|50|11.771 / 11.878|7.791 / 7.908|-3.980 (-33.81%)|
|fix|150|57.147 / 58.257|22.685 / 23.183|-34.462 (-60.30%)|
|fix|500|448.939 / 459.182|82.959 / 84.596|-365.981 (-81.52%)|

At H500, normal CPU p50 (ms) was native44.499→45.333, operational435.405→73.142 and FIX448.466→82.545. Peak traced Python allocation (MiB) was0.7703→0.3751,1.1626→0.3642 and1.1683→0.3655 respectively (−51.30%,−68.68%,−68.72%); this is not RSS/native SQLite allocation. Separate phase counters: operational/FIX copy roots383.672→27.435ms /389.340→30.111ms; replay441.155→76.716ms /452.810→86.170ms. Recursive copy calls3,352,624→351,714 /3,418,137→398,380. Inclusive phase costs overlap; do not add them. Commit cost remained about0.55–0.65ms; its fluctuations are not attributed to this change.

The original H500 operational/FIX p50 savings83.08%/81.50% reproduced as83.12%/81.52%. Native regressed: original H500+0.690ms/+1.53%, revalidation+0.880ms/+1.96%; H50/H150 also show small regressions. H10 is inconclusive. Revalidation exploratory paired-bootstrap savings intervals at H500: operational83.02–83.29%, FIX81.42–81.62%, native−2.52 to−0.69%;20,000 resamples with adjusted tails across12 submit cells, assuming exchangeable pairs. Host thermals, scheduling and cache drift are uncontrolled. A roughly20-minute log gap occurred in revalidation round0; cause unconfirmed, all samples retained, no command timer recorded it. Sensitivity excluding that round still gives83.137%/81.512% operational/FIX savings and1.786% native slowdown. No universal or traffic-weighted speedup, stable tail latency, cold-disk, contention, wide-Unicode workload or network result is claimed. Repeated list-prefix copies and full replay remain; no asymptotic improvement is claimed.

### Verify or rerun from public inputs

The public entry point and its two helpers are a path/metadata adaptation of the measured harness. Timed workload, accounting checks and diagnostic functions are retained; default full mode uses the original sample schedule. The new smoke mode checks execution only. The published adapter was smoke-tested on macOS Python3.11/3.13; its full long run was not repeated during publication cleanup. Nine timed/validation functions and classes were checked AST-identical to the measured harness, and the full default randomized schedule matches the saved observation order. Fresh timings will differ across environments; no exact timing match is promised. No saved database is required or forced across source versions.

```bash
python scripts/benchmark_history_ab.py --verify-evidence
# Use a full Git checkout containing both pinned source commits. If shallow:
git fetch --unshallow origin
# Choose a NEW directory outside every Git repository:
python scripts/benchmark_history_ab.py --output /tmp/NEW_OMS_AB_SMOKE --smoke
python scripts/benchmark_history_ab.py --output /tmp/NEW_OMS_AB_FULL
```

Run the fetch only for a shallow checkout. Full runs take tens of minutes and produce new latency/summary/profile/memory/phase JSONs and source-compatible fixtures externally. Smoke timings are not performance evidence. Python 3.11+ on macOS/Linux, Git and the standard library suffice for this harness; neither private market data nor API keys are used. Do not use Python -O. Missing pinned Git objects fail before creating output; verification of the published projections also works from the source distribution without Git history.

## Historical development evidence

## 대표 실행과 설계 판단

`python examples/operational_outage.py`는 기존 durable_order_recovery의 실제 FundedJournal,
FundedOrderManager, SimulatedVenue를 사용한다. 별도 주문/계정 엔진을 만들지 않았다.
POSIX 자식 클라이언트만 SIGKILL하며 부모의 합성 거래소는 살아 있다.
시세 시간은 0, TTL은 10ns, 최초 승인은 10ns로 고정한다. 실제 시간은 성능 측정에만 사용한다.
체결은 수동 지정한 3@99와 2@100이며 호가 매칭·확률·수수료 모델이 아니다.

최초 전액 예약 → 응답 유실과 부재 조회 → 첫 부분 체결 조회 → 11ns의 stale/누락
시세에 신규 매수 거부 → 취소 중 추가 체결 및 응답 유실 → 강제 종료 → 재시작
→ 누적 조회 → 지연·중복·역순 보고의 순서다. 첫 부분 체결 뒤 신규 거부를 검사하는 이유는
사용 가능 현금 3을 확보해 자금 부족과 시세 장애를 분리하기 위해서다.
그 뒤의 추가 체결·취소·조회는 시세를 갱신하지 않아도 계속 처리된다.
출력은 각 체크포인트의 상태·현금·예약·보유 수량과 이유를 보여준다.

손계산은 현금 `1000-3*99-2*100=503`, 보유 5, 종료 후 예약 0이다.
별도 Decimal 계산으로 거래소 체결 ID와 사실을 모아 계산하며 OMS 회계 함수를 호출하지 않는다.
틀린 현금·중복 체결·잔여 예약·재시작 불일치는 예외와 비정상 종료를 일으킨다.
대사는 이 합성 주문의 거래소 사실과 로컬 계정 비교이며 실제 계좌 전체 조회가 아니다.

직접 설명해야 할 판단 세 가지:

1. `funded_journal.py`의 `_record`: BEGIN IMMEDIATE 안에서 재생·명령·스냅샷을 커밋한
   뒤 outgoing을 반환한다. 커밋과 네트워크 전송은 원자적이지 않으므로 미응답은 재발송 대신 조회한다.
2. `funded_orders.py`의 `submit`과 `apply`: 신규 노출 승인에만 시세를 요구한다.
   기존 보고 반영과 취소는 시세 장애로 막지 않는다. 미응답 예약은 부재 조회만으로 해제하지 않는다.
3. `funded_journal.py`의 `_verify`: 저장 스냅샷을 믿지 않고 모든 명령 결과를 다시 계산한다.
   중복 보고 처리는 order_lifecycle의 revision·execution ID 규칙에 맡긴다. 정확성의 대가가 아래 비용이다.

## 변경 전 기준 측정

재현: `python scripts/benchmark_funded_journal.py --output benchmarks/funded-baseline.json`
(기존 파일 보존 시 다른 출력 파일을 지정한다.) 원시값·환경·코드 해시·git 상태·SQLite PRAGMA는
[측정 JSON](../benchmarks/funded-baseline.json)에 있다. 코어는 이번 작업에서 수정하지 않았다.
각 규모마다 새 DB 3개를 순차 생성하고, 거부된 신규 명령 N개를 쌓은 뒤 승인 주문 1개,
전량 체결 보고 1개, close/open을 측정한다. seed 생성과 거래소 연산은 측정에서 제외한다.
open은 전체 검증 1회이며 open 뒤 recover 호출은 포함하지 않는다.

| 선행 명령 수 | 신규 승인 중앙값 ms | 보고 반영 중앙값 ms | open 복구 중앙값 ms |
|---:|---:|---:|---:|
| 10 | 3.326 | 1.566 | 1.136 |
| 50 | 3.414 | 5.189 | 3.375 |
| 150 | 8.995 | 9.183 | 8.982 |

Python 3.13.15, macOS, SQLite FULL synchronous, delete journal이다. 정확한 버전은 JSON에 기록한다.
최소/최대와 개별 표본도 보존했다. 3회는 꼬리 지연 통계를 추정하기에 부족하다.
캐시/스케줄링/I/O 변동이 있으며 warmup을 별도로 하지 않았다. 10개 신규 승인의 최대는
12.347ms여서 중앙값만으로 안정성을 주장하지 않는다. CI에는 시간 합격선을 두지 않는다.

`_record`, `recover`, `assess`, `open`은 실제로 `_verify`에서 전체 명령을 재생한다.
이 실험은 살아 있는 주문 수를 고정해 이력 증가의 비용만 분리한다. 다수 활성 주문·큰 누적
보고·장시간 운전의 비용은 더 클 수 있다. 이 수치는 실거래 레이턴시나 HFT 성능이 아니다.

이번 일정에는 성능 코어를 바꾸지 않았다. 단순 메모리 캐시는 다른 연결의 커밋과 DB 변조를
놓칠 수 있고, 체크포인트 신뢰는 기존의 전체 재생 검산 계약을 바꾼다. 현재 작은 규모의
측정만으로 이 안전성 비용을 감수할 근거가 부족하다.
후속 작업은 (1) 다수 활성 주문 부하 분리 측정 (2) 검증된 prefix와 외부 writer 변경 감지
(3) 체크포인트와 전체 감사의 명시적 계약 및 기존 DB 이관 (4) 변조·동시 writer·크래시
회귀와 동일 부하 전후 비교 순서다. 현재 DB 코드 해시 검증·내구성·중복 검출은 유지한다.

## 이번 로컬 검증 기록

브랜치 `feat/oms-operational-evidence`, 기준 HEAD `885e86967ad4b6ee462ed1e02e48279be6eb1335`.
기존 README 미커밋 변경을 유지하고 그 위에 대표 실행·근거 링크를 추가했다.
적용할 AGENTS.md는 프로젝트와 상위 경로에서 발견되지 않았다.

- 변경 전 `.venv/bin/python scripts/verify_project.py`: 589 passed, skip 0, 예제 15개 성공.
- 변경 후 같은 명령: 590 passed, skip 0, 예제 16개 성공, 로컬 링크 및 pip check 성공.
- `python -m venv /tmp/oms-evidence-clean` 후 해당 Python으로 `pip install '.[dev,historical]'`:
  비편집 설치 성공. `/tmp`에서 대표 operational_outage.py 실행과 독립 검산 성공.
- `git diff --check`: 성공. 원격 CI는 수정했으나 이번 변경에 대해 실행하지 않았다.

추가 테스트는 시세 장애와 기존 복구 흐름의 연결을 검사한다. TTL 세부 경계는 기존
`tests/test_funded_orders.py`, 미응답 예약·중복 보고·오류 원장 검출·실제 SIGKILL은
`tests/test_order_recovery_e2e.py` 및 `tests/test_funded_journal.py`도 함께 실행한다.
성능 최적화가 없으므로 전후 성능 향상 주장은 없다. 새 플랫폼·전원 손실·실계좌·대규모
부하 검증은 수행하지 않았다.

## Cancel-Fill Race 검증 확장

추가 실행: `python examples/cancel_fill_race.py`.
수량 10, 지정가 100, 현금 1000에서 4@99 체결 → 잔여 6 취소 요청 → 3@100 추가 체결
→ 잔여 3 취소 확정이다. 손계산은 현금 `1000-396-300=304`, 보유 7, 예약 0이다.
데모의 재시작은 DB close/open이다. 실제 프로세스 비정상 종료는 자동화 테스트와 기존
SIGKILL 데모에서 별도로 검사한다. 외부 주문 전송·실제 거래소 연결은 없다.

### 코드에서 확인한 기존 구조와 미구현 범위

| 책임 | 코드/기존 동작 |
|---|---|
| 주문 상태 전이 | `order_lifecycle.py: OrderManager.apply`, `order_protocol.py: state/report` |
| 승인·회계 불변식 | `funded_orders.py: submit/_action`, `order_reservations.py: account` |
| 거래소 누적 사실 | `simulated_venue.py: execute/cancel/query`; 체결은 호출자가 수동 지정 |
| 트랜잭션·복구 | `funded_journal.py: _record/_verify/recover/reconcile` |

로컬 승인 직후 `pending_new`, 응답 불명은 `unknown`이다. 거래소 상태는 `open`,
`partially_filled`, `filled`, `canceled`, `rejected`로 구분한다. 취소 요청은 주문 상태를
바꾸지 않고 별도 `cancel_status=pending`으로 남긴다. 부재 조회는 `unknown`, 취소 확정은
`canceled`, 전량 체결이 먼저 끝나면 `too_late`가 된다. 신규 주문 거부는 체결 0인
`rejected`이며 이미 승인된 주문을 신규 거부로 되돌릴 수 없다.
`remaining_quantity`는 원수량-체결량이다. `canceled`에서만 이를 취소 잔량으로 해석한다.
활성 주문의 remaining은 취소 수량이 아니다. 별도 canceled_quantity 필드는 추가하지 않았다.

보고 처리 순서가 달라도 수렴하는 이유는 **같은 거래소의 유효한 누적 사실**이라는 계약이다.
높은 revision은 이전 체결 ID를 모두 포함해야 한다. 낮은 revision은 이미 아는 체결의
부분집합이어야 하며 상태를 되돌리지 않는다. Execution ID의 수량·가격·주문 귀속이 바뀌면
거부한다. 종료 사실도 바뀔 수 없다. 중복 event ID와 별도 ID로 재전달된 같은 누적 사실도
회계를 중복 반영하지 않는다. 임의로 모순되는 보고까지 수렴시키는 계약은 아니다.

자금 예약은 비종료 매수의 잔여수량×지정가다. 따라서 취소 요청만으로 예약을 풀지 않으며,
체결이 확정되면 체결 원가가 현금에서 차감되고 미체결 예약만 줄어든다. FULL synchronous의
SQLite BEGIN IMMEDIATE 안에서 재생 → 후보 검증 → 명령 저장 → 스냅샷 저장 → COMMIT을
수행하고 그 뒤 outgoing을 반환한다. 롤백은 명령·체결·회계를 함께 되돌린다.
코어의 상태 전이·저널 스키마·해시 검증은 이번에도 변경하지 않았다.

### 대사와 불확실성

승인 응답 유실·미수신 체결·취소 중 전량 체결은 기존 누적 조회로 복구할 수 있다.
`found=True`는 조회가 주문을 찾았다는 뜻이며 취소 결과 확정을 뜻하지 않는다.
오래된 open 조회 뒤에도 `recover().reconciliation`에는 대상이 남고 cancel_status는
unknown이다. 부재 조회만으로 미접수/취소 완료를 추정하지 않는다. 최신 종료 조회가 오면
누락 체결을 함께 반영해 예약을 해제한다. 상충하는 권위 기록은 예외로 거부하고 기존 DB를
유지한다. 충돌을 별도 영속 incident 상태로 보관하거나 운영자에게 알리는 기능은 없다.
실제 거래소의 페이지 누락·revision 부재·체결 정정·수수료·수동 거래·계좌 전체 대사는 미구현이다.

### 자동화 검증

`tests/test_cancel_fill_race.py`는 다음을 검사한다.

- ack / 4개 체결 / 7개 체결 / 취소 확정 보고의 **24개 전순열 × 재시작 경계 5개**.
  취소 요청 이전에는 4개 체결을 알아야 한다는 인과관계를 유지한다. 각 보고 직후 독립적인
  Decimal 계산으로 현금·보유·예약·Execution 수를 검사하고 같은 보고를 재전달한다.
  각 스케줄의 최종 snapshot도 서로 비교한다. 무작위·실제 병렬 테스트가 아니다.
- 대사 도중 snapshot 쓰기 전, snapshot 쓰기 후 COMMIT 전, COMMIT 직후 자식 프로세스의
  `os._exit(73)`를 주입한다. 재시작 시 전부 미커밋 또는 전부 커밋만 허용하며 동일 명령 재시도로
  종료 대사가 수렴하는지 검사한다. 정상 close/open과 비정상 종료를 구분한다.
- 취소 중 부재·stale open 조회가 예약 600과 unresolved를 유지하는지, 체결 사실 충돌이
  상태를 바꾸지 않는지 검사한다.

명령 영속화·거래소 체결 직후 종료, 체결·취소 보고의 커밋 전후 종료, 전량 체결이 취소를
이기는 시나리오는 기존 `tests/test_funded_journal.py`도 실행한다. 네트워크 패킷 전달과
전원 손실을 모사한 것은 아니다.

`tests/test_operational_outage.py`에 동일한 저가 주문의 TTL=10 승인, TTL+1 거부, 시세 누락
거부를 별도 DB에서 비교하는 회귀를 추가했다. 사용 가능 현금이 충분함을 보장해 이전
`Quote outage failed to block new exposure`의 원인이었던 자금 부족 우선 거부와 분리했다.
해당 오류는 시세 통제 누락이 아니라 데모 입력이 다른 거부 사유를 먼저 유발한 문제였다.

전체 검증 명령은 examples를 자동 발견하고 전체 테스트를 실행하므로 새 데모와 검사가 기존
CI 경로에 포함된다. 시간 수치를 CI 합격선으로 사용하지 않는다.

## 확장 성능 측정 — 조건별 20회

원시 표본은 [이력](../benchmarks/funded-history-20.json), [활성 주문](../benchmarks/funded-active-20.json), [누적 체결](../benchmarks/funded-fills-20.json)에 보존했다.

| 선행 명령 / 활성 주문 / 주문당 체결 | 신규 승인 | 보고 반영 | open 복구 | recover |
|---|---:|---:|---:|---:|
| 10 / 0 / 0 | 1.005 / 1.036 | 1.091 / 1.141 | 0.981 / 1.036 | 0.902 / 1.063 |
| 55 / 0 / 0 | 3.481 / 3.564 | 3.524 / 3.673 | 3.406 / 3.449 | 3.304 / 3.334 |
| 150 / 0 / 0 | 8.729 / 9.101 | 8.582 / 8.908 | 8.550 / 8.741 | 8.272 / 8.507 |
| 55 / 5 / 0 | 3.860 / 4.094 | 4.031 / 4.543 | 3.853 / 3.969 | 3.668 / 4.120 |
| 55 / 5 / 3 | 6.800 / 7.139 | 7.015 / 7.432 | 6.928 / 7.089 | 6.748 / 7.013 |

단위 ms, 각 셀은 p50(중앙값) / 표본 p95(nearest rank)다. 각 조건 20개 표본으로 p95는
19번째 값에 불과하며 안정적인 꼬리 추정이 아니다. p99는 100개 미만에서 null로 기록한다.
이는 100개면 신뢰성이 보장된다는 뜻도 아니다. 별도 warmup 없이 순차 실행했고 이 최종 측정
중 다른 프로젝트 검증/벤치마크 작업을 병렬 실행하지 않았다. OS background 부하와 캐시는 통제하지 않았다.
환경은 Python 3.13.15 / macOS 26.6.2 arm64 / SQLite 3.53.4, delete journal, synchronous=2(FULL),
busy_timeout=5000ms, page_size=4096이다. 각 파일에 코드/측정 스크립트 SHA256과 Git 상태를 기록했다.

선행 명령 수는 probe 승인 직전이다. 보고 반영 시에는 +1, open/recover 시에는 +2다.
활성 주문은 probe 이전 수이며 수량 20으로 제출해 각 체결 수가 0 또는 3이어도 활성 상태를 유지한다.
55명령에서 활성 0 / 활성 5 / 활성 5+각 3체결을 비교한다. 마지막 경우 rejected seed를 35개로
줄여 전체 명령 수를 55로 맞췄다. 이벤트 종류와 상태 크기가 함께 달라지므로 단일 요인의 순수한
인과효과나 일반적인 복잡도 증명으로 해석하지 않는다. 각 JSON raw에는 seed 수와 전체 수를 모두 보존한다.

`submit_verify_ns` / `apply_verify_ns`는 `_verify`를 측정용 wrapper로 감싼 내부 구간이다.
이 wrapper는 코어를 수정하지 않는다. 전체 구간에는 wrapper 오버헤드도 포함된다. open 구간은
연결 생성·전체 재생·읽기 커밋·close를 포함한다. recover는 열린 연결의 읽기 재생·snapshot·대상 목록
생성과 커밋이다. 주문/보고 구간에는 검증·후보 적용·명령/스냅샷 쓰기·커밋을 포함하고,
seed 생성과 거래소 실행은 제외한다. 표본별 내부/전체 시간을 서로 빼면 나머지 경로 비용을 확인할 수 있다.

150명령에서 신규 승인 전체 p50 8.729ms 중 검증 p50은 8.292ms다. 두 중앙값은 같은 표본의 합으로 해석하지 않는다.
전체 재생이 지배적인 경로임을 관측했지만, 검증 약화 없는 캐시/체크포인트 설계는 이번에도 보류했다.
실거래 레이턴시·거래소 RTT·처리량 측정이 아니다. 재현 명령은 다음과 같다.

```bash
python scripts/benchmark_funded_journal.py --output /tmp/history.json --sizes 10 55 150 --repeats 20
python scripts/benchmark_funded_journal.py --output /tmp/active.json --sizes 50 --repeats 20 --active-orders 5
python scripts/benchmark_funded_journal.py --output /tmp/fills.json --sizes 35 --repeats 20 --active-orders 5 --fills-per-order 3
```

확장 후 `.venv/bin/python scripts/verify_project.py`: 596 passed, skip 0, 공개 예제 17개 성공.
이 수치에는 이전 590개와 새 경합·대사·시세 회귀가 포함된다. 원격 CI는 이번에도 실행하지 않았다.

## 영속 Incident와 운영 통제 검증

이번 기능의 계약·아키텍처·운영 절차는 [OMS_OPERATIONS.md](OMS_OPERATIONS.md)를 따른다.
기존 주문 상태 머신은 변경하지 않고 `OperationalJournal`을 선택형 별도 schema로 추가했다.
`funded_journal.py`에는 manager/reducer/schema 선택 지점을 추가해 동일 BEGIN IMMEDIATE,
전체 재생·결과 검산·스냅샷 저장·커밋 경로를 재사용한다. 별도 Incident 커밋 경계는 없다.
코드 정의 해시에 새 운영 모듈을 포함한다. 이전 코드로 생성한 DB 자동 이관은 지원하지 않는다.

추가한 `tests/test_oms_operations.py`의 13개 사례는 사건 생성/대사 각각의 커밋 경계 3개,
중복 억제·조사/해결 이력·재시작 보존·제한 중 보고/취소/매도·정상 복귀 거부·기존 API 우회 거부,
모드 전환과 접수의 두 연결 경합, 변조 검출과 실제 SIGKILL 데모/CLI를 포함한다.
경합 테스트는 실제 두 스레드/별도 연결을 사용하므로 승자를 고정하지 않고 커밋 sequence로
허용 결과를 판정한다. 사건/대사 종료 주입은 실제 자식의 os._exit이며 전원 손실 검증은 아니다.

실행 명령 `.venv/bin/python scripts/verify_project.py`: **609 passed, skip 0, 예제 18개 성공**.
이전 596개 회귀를 유지했고 실제 시세 장애 데모와 TTL 거부 회귀도 포함했다.
`git diff --check`도 검사한다. 이번 변경의 원격 Linux CI는 실행하지 않았다.

E2E `python examples/incident_recovery.py`는 부모 모의 거래소를 유지한 채 자식 OMS만 SIGKILL한다.
로컬 4개 체결/취소 pending, 거래소 7개 체결/잔여 3 취소의 구체적인 차이를 기한 전에 일시적
지연으로 분류하고, 기한 5ns 후 Incident로 저장한다. RESTRICTED 동안 신규 위험을 거부하고
누락 체결을 누적 조회로 반영한다. 중복/역순 보고 후 재검증해 사건 RESOLVED와 NORMAL 복귀를
확인한다. 최종 독립 계산은 현금304·보유7·예약0이다.
진단 CLI가 주문 ID·query event ID·command ID·Incident ID를 연결해 보여준다.
자동 외부 알림이나 실제 네트워크 timeout 탐지를 구현했다고 주장하지 않는다.

기존 성능 표와 JSON은 각 파일에 기록된 당시 코드 해시의 측정이다. 새 운영 경로는 사건
증거와 운영 이력도 매번 재생하고 스냅샷에 저장하므로 비용이 추가된다. 이번 단계에서는
이 경로의 성능을 측정하지 않았고 이전 수치를 새 경로에 적용하지 않는다.
정확성 검증을 줄이는 성능 개선은 하지 않았다.

별도 `/tmp/oms-evidence-clean` 가상환경에 현재 패키지를 비편집 재설치한 뒤 `/tmp`에서
새 Incident 데모를 실행해 SIGKILL·최종 독립 검산 성공을 확인했다. 새 환경의 운영 경로
실행 확인이며 새 Linux/Windows 검증이나 해당 환경의 전체 회귀 실행을 뜻하지 않는다.

## Startup Recovery와 FIX 필드 연동

이번 실행 경로와 제한은 [FIX_ADAPTER.md](FIX_ADAPTER.md)에 정리했다.
기존 저널을 저장소 API로 유지하고 실행용 TradingSession에 POSIX .owner.lock을 추가했다.
정상 close 여부를 기록하되 항상 새 RECOVERY/조회 epoch를 커밋한 뒤 핸들을 반환한다.
macOS에서 DB 파일 자체 flock이 SQLite 연결과 충돌한 실패를 재현했고 별도 락 파일로 분리했다.
최초 Startup 테스트 3건이 이 원인으로 실패했으며 보호 조건을 완화하지 않고 수정했다.
두 번째 실제 프로세스는 저널 변경 전에 락 획득에 실패한다. 협력 API를 쓰지 않는 직접 DB 쓰기,
파일 교체·분산 fencing·Windows·네트워크 파일시스템은 보장하지 않는다.

단계별 실행:

- `.venv/bin/python -m pytest -q tests/test_startup_recovery.py tests/test_oms_operations.py`:
  수정 후 16 passed. 이후 startup 커밋 전후 종료 3건을 추가했다.
- `.venv/bin/python -m pytest -q tests/test_startup_recovery.py tests/test_fix_adapter.py`:
  20 passed 시점에서 startup/FIX 커밋 종료와 필드·변환·순서 검사를 확인했다.
- E2E subprocess 회귀 추가 후 `.venv/bin/python scripts/verify_project.py`:
  **630 passed, skipped=0, 공개 예제 19개 성공**. 기존 609개를 유지한다.
- 대표 `python examples/fix_order_recovery.py`: 실제 자식 SIGKILL=-9, Startup RECOVERY,
  누락 Trade 보류·Incident·역순/중복 archive 재전달·상태 조회·NORMAL 복귀와 독립 원장 검산 성공.

FIX는 실제 바이트/세션이 아닌 제한된 FIX 4.4 필드 객체다. 35=D/8/F와 종료 주문의 좁은 35=9를
지원하고 35=G는 제외한다. CumQty/LeavesQty/AvgPx/주문 ID와 실제 Trade ExecID를 검증한다.
지원 프로파일의 24개 전달 순서가 같은 최종 회계로 수렴한다. 유효하지 않은 필드 조합과
모순된 체결은 롤백하며, 누락 이력은 추정하지 않고 영속 보류한다. FIX 원본 메시지 book과
체결 회계의 커밋 전후 자식 종료도 검증한다. 연결 단절은 응답 미전달, 재전달은 fixture archive이며
FIX Logon/Heartbeat/Resend/Gap Fill을 구현했다고 주장하지 않는다.

순수 변환 측정: `python scripts/benchmark_fix_adapter.py --output /tmp/fix-conversion.json`.
[원시값·환경·코드 해시](../benchmarks/fix-field-conversion.json)를 보존했다. Python 3.13.15,
macOS arm64, warmup 50회 후 1000회, perf_counter_ns wall time이며 단위는 아래 µs다.

| 구간 | p50 | 표본 p95 | 표본 p99 |
|---|---:|---:|---:|
| 신규 주문 필드 생성+parse 왕복 | 5.417 | 6.042 | 6.167 |
| 5개 보고에서 누적 도메인 보고 재구성·검증 | 141.521 | 152.750 | 158.875 |

표본 p95/p99는 nearest rank다. 단일 프로세스·고정 작은 이력·warm cache 관측이며 안정적 꼬리
지연 보장이나 HFT 성능이 아니다. fixture 구성과 거래소 처리·SQLite·fsync·저널 재생·소유권 획득·
네트워크는 제외하고 함수의 검산 assertion은 포함한다. 최신 어댑터 비용과 저장 경로 비용을
더하거나 예전 약9ms를 현재 전체 레이턴시로 사용하는 것은 부적절하다. 환경 독립 CI 시간 기준은 없다.

설계상의 주요 Trade-off는 (1) clean 종료도 미수신 거래소 사실의 부재를 증명하지 못하므로 항상
재대사 (2) FIFO 전송을 가정하지 않되 실제 Trade ID가 없는 CumQty/AvgPx만으로 회계를 만들지 않음
(3) 기존 누적 도메인을 유지하기 위해 제한된 FIX 프로파일과 재생 비용을 수용한 점이다.

최종 신규 거부 보고의 예약 해제 회귀를 추가한 뒤 전체 검증은 **631 passed, skip 0, 예제19개 성공**이다.
별도 비편집 설치 환경에서 저장소 밖의 FIX 데모도 실행했다. 새 원격 CI 실행·전원 손실·실연결 검증은 수행하지 않았다.

## 최종 엔지니어링 감사 결과

이 절은 이전 FIX 엔지니어링 감사의 기록이다. [감사 당시 문서](ENGINEERING_AUDIT.md)와
[원시 측정](../benchmarks/engineering-audit.json)을 해당 시점의 소스/테스트와 함께 읽는다.
기능을 추가하기보다 기존 필드 변환의 결함을 재현하고 국소 수정했다.

- 비조회 ExecID=0과 조회 sentinel 혼동 가능성: 상태 조회 전용으로 제한.
- 불완전 prefix의 잘못된 가격/lot 체결이 버퍼에 남는 문제: 기존 protocol.state로 선검증.
- 내부 이벤트 namespace 충돌 및 최대 길이 ExecID 거부: namespace별 고정 길이 SHA256 매핑.
- 호출자의 Decimal rounding에 따른 AvgPx 변화: HALF_EVEN 명시.

최초 최소 재현 4개는 모두 실패했고 수정 후 통과했다. 추가한 두 검증은 Incident 재발 에피소드와
seed 0..11의 매 단계 현금/수량/예약 모델 검사다. 새 의존성이나 도메인 상태 머신은 추가하지 않았다.
현재 `.venv/bin/python scripts/verify_project.py`는 **637 passed, skip0, 공개 예제19개 성공**이다.
시세 장애·기존 커밋 종료·Startup/FIX/SIGKILL 회귀를 모두 포함한다. 원격 CI는 실행하지 않았다.
`git diff --check`와 별도 비편집 설치 경로도 최종 점검한다. commit/push는 하지 않는다.

새 벤치마크는 6조건×20개의 별도 DB에서 메모리 주문 reducer/FIX 변환/신규 저장/보고/대사/
Incident/Startup/전체 검산을 분리한다. 이력150의 신규 저장 p50=49.512ms, 전체 검산48.295ms,
Startup152.671ms다. 운영 후보 복사와 FIX 이력 재검산까지 들어간 현재 비용이며 기존 약9ms를
재사용하지 않았다. 표본 분산·p95·원시값을 남기고 표본20에서 p99는 생략했다. 성능 코어는 바꾸지 않았다.
