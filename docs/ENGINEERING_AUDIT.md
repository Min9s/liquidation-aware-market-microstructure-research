# 최종 엔지니어링 감사

감사 범위는 현재 작업 브랜치의 Python 합성 OMS이며 상용 운영 인증이나 전수 오류 부재 증명이 아니다.
기존 미커밋 변경을 보존했고 commit/push하지 않았다. 적용할 AGENTS.md는 발견되지 않았다.
상태/회계/저널/운영/FIX/소유권 코어와 관련 테스트·대표 예제·README·운영 문서를 중심으로 읽었다.
기존 연구/IOC 경로는 문서 및 전체 회귀로 확인했으며 모든 연구 알고리즘을 다시 증명하지 않았다.

## 발견 사항과 조치

| 심각도 | 발견 | 조치/잔여 위험 |
|---|---|---|
| Critical | 이번 검토에서 재현한 Critical 없음 | 테스트 통과가 모든 결함의 부재를 뜻하지 않음 |
| High | ExecID=0인 비조회 보고도 허용해 조회용 sentinel과 혼동 가능 | 0은 상태 조회에만 허용; 수신 전 무변경 거부 회귀 |
| High | 누락 prefix에서 조기 반환해 지정가 위반 Trade가 영속 보류될 수 있음 | prefix와 독립적인 체결 검증을 기존 protocol.state로 먼저 수행 |
| High | 보고 `fix-<ExecID>`와 조회 `fix-query-<command>` 이름 공간 충돌 가능 | 서로 다른 namespace + SHA256 고정 길이 이벤트 ID. 원본 ID는 저널에 유지 |
| Medium | 허용 길이100 ExecID가 내부 접두사 때문에 길이 제한을 초과 | 고정 길이 매핑으로 해결. 최대 길이 회귀 |
| Medium | AvgPx HALF_EVEN 가정이 호출자 Decimal rounding 변경에 종속 | quantize에 rounding을 명시; ROUND_DOWN 호출 환경 회귀 |
| Medium | 저수준 journal API는 실행 소유권을 우회할 수 있음 | 기존 계약 명확화. 지원 실행 진입점은 TradingSession; 권한 fencing은 미구현 |
| Medium | 전체 재생 및 FIX prefix 재검산 비용, 증거/로그 무한 증가 가능 | 현재 코드의 새 측정 보존. 체크포인트/이관 없이 검증을 줄이지 않음 |
| Medium | FIX/운영 reducer가 funded._manager 등 private 상태에 결합 | 별도 도메인 재구현은 피했지만 내부 변경에 민감. 대규모 리팩터링 보류 |
| Medium | dict 모델과 런타임 검증 중심, 타입 힌트 일관성/정적 타입 검사 부족 | 불변 계약은 명시돼 있으나 TypedDict 등 도입은 후속. 이번에 전역 타입 전환하지 않음 |
| Low | 긴 reducer 분기·유사한 예제 fixture·공개/내부 함수 구분이 약함 | README에서 안정 실행 API와 내부 reducer를 구분. 동작 변경 없는 정리는 보류 |
| Low | 새 미공개 기능과 기존 v0.2.0/589개 결과가 README에 섞임 | README를 현재 checkout 중심으로 다시 구성, 과거 측정/릴리스 기록은 상세 문서로 이동 |

`tests/test_audit_adversarial.py` 최초 4개 재현 테스트는 모두 실패했다. 수신 거부, 최대 ID,
외부 Decimal 설정을 실제로 실행해 확인했다. 문자열 namespace의 충돌은 코드상 동일한 이름이
생성되는 것으로 확인하고, 수정 후 실제 trade/query 공존 경로를 검증했다.
위 결함에서 현금 초과 지급을 재현했다고 주장하지 않는다. 잘못된 입력의 영속 보류와 대사
식별 오류가 복구를 방해하거나 잘못된 조회 선택을 유발할 위험을 먼저 제거했다.

## 책임·예외·API·중복 검토

OrderManager는 주문/취소 상태·보고/체결 ID·revision, FundedOrderManager와
order_reservations는 현금·보유/예약·노출 검사, FundedJournal은 원자적 명령/스냅샷 저장을 맡는다.
OperationalJournal은 Incident/정상 복귀 증거, FixJournal은 필드→누적 사실 연결,
TradingSession은 협력 프로세스 소유권과 startup를 맡는다. 중복 회계 엔진은 추가하지 않았다.
FIX 필드와 도메인 양쪽 검증은 경계가 달라 의도된 방어이며 체결 tick/limit 검사는 protocol을 재사용한다.

BaseException catch는 rollback/close 후 재발생시키는 정리 경계다. 성공으로 바꾸거나 숨기지 않는다.
comparison의 ValueError는 검증된 조회의 도메인 모순을 Incident로 전환하는 의도적 분기다.
입력 형식/주문 귀속 오류는 여전히 예외다. 실패 트랜잭션 에러의 영속 수집기는 없다.
정적 dead-code 분석을 완료했다고 주장하지 않는다. 사용하지 않는 큰 프레임워크/새 의존성은
도입하지 않았고 핵심 클래스는 테스트와 공개 실행 경로에서 사용한다.

지원 실행: TradingSession.create/open(journal_type=FixJournal), submit, receive, cancel_fix,
reconcile_fix, control, diagnostics. journal._record/_verify 및 reducer는 내부 구현/검증 도구다.
저수준 journal 직접 쓰기는 소유권 보호 경로가 아니며 raw SQLite 쓰기도 차단하지 못한다.
FIX는 필드 객체이며 Python API에서만 변환한다. CLI의 OperationalJournal 진단은 운영 schema용이며
FIX DB에는 Python FixJournal.diagnostics를 사용한다. 모든 schema를 여는 범용 CLI는 없다.

## 회계 불변식

수수료0·단일 통화·차입/공매도 없는 현금 계정에 한정한다. 각 ExecID의 불변 signed quantity를 s,
가격을 p라 하면 Cash = InitialCash - Σ(s×p), Inventory(symbol)=Σs다.
Cash = AvailableCash + ReservedCash, Inventory = AvailableInventory + ReservedInventory이며
AvailableCash/AvailableInventory는 음수가 아니다. 가격 개선 때문에 체결 대금은 예약 감소와 같지 않을 수 있다.

| 주문 상태 | 수량 보존 | 예약 |
|---|---|---|
| pending_new/unknown/open/partial | Q = Filled + Remaining | 매수 Remaining×Limit, 매도 Remaining units |
| cancel pending/unknown | 같은 식; 취소 요청은 종료가 아님 | 기존 잔여 예약 유지 |
| filled | Q = Filled, working=0 | 0 |
| canceled | Q = Filled + Canceled, working=0 | 0 |
| rejected | Filled=0, working=0; 미접수 잔량 Q는 rejected 수량 | 0 |

스냅샷의 remaining_quantity는 항상 Q-Filled다. canceled에서는 그 값이 취소 잔량이며,
**Filled+remaining_quantity+canceled를 더하면 중복 계산**이다. rejected 잔량을 canceled로 부르지 않는다.
일반화하면 Q=Filled+Working+Canceled+Rejected/Unaccepted이고 각 상태에 맞춰 항을 분리해야 한다.
같은 ExecID는 한 사실로만 합산하고 다른 사실은 무변경 거부한다. 명령·체결·예약 스냅샷을 한
트랜잭션으로 커밋한다. raw 저널의 committed_commands와 세션 epoch/프로세스 ID는 전달 순서에
따라 달라질 수 있으므로 순서 독립성을 주문/계정의 동일 유효 사실 집합에 대해 주장한다.

독립 원장은 venue의 fill facts를 Decimal로 합산하고 OMS account/replay 함수를 호출하지 않는다.
다만 SyntheticFixVenue의 필드 생성과 어댑터는 일부 helper를 공유한다. 따라서 E2E만으로 FIX
변환 독립성을 주장하지 않고 수동으로 변조한 필드·원수량/지정가·고정 손계산을 추가로 검사한다.

## Adversarial 경계와 기대 동작

| 입력/장애 | 기대 및 근거 |
|---|---|
| 같은 ExecID의 다른 사실 | 거부·롤백; test_fix_adapter와 audit 재현 |
| 같은 OrderID/다른 ClOrdID | 도메인 binding 거부; test_order_lifecycle/order_safety |
| 초과 체결·잘못된 LeavesQty·모순된 CumQty | 거부. 낮은 CumQty라도 유효한 과거 prefix는 stale 처리 |
| 취소 직후 늦은 체결 | 실제 체결 반영, 취소 확정 전 예약 유지; cancel_fill_race |
| 누락 보고 | 영속 보류·RECOVERY; 기한 이후 조회 부재 Incident |
| 대사 후 새 보고 | 조회 증거 무효화. NORMAL 재전환에는 재조회 필요 |
| 복구 중 두 번째 실행 프로세스 | 파일 락 획득 실패; startup_recovery |
| 복구 완료 전 신규 위험 | outgoing 없음; startup/oms_operations |
| Incident 해결 직후 재발 | 새 ID/OPEN 사건 생성, NORMAL 거부; audit recurrence |
| 저널 변조·불완전 데이터 | 열기/검산 실패. 자동 수리·예약 해제 없음; funded_journal |
| 주문 결과 불명 | 조회 부재로 실패 확정/재발송하지 않고 예약 유지 |

기존 24 전순열/5 재시작 경계에 더해 audit 모델은 고정 seed 0..11, 세 번의 서로 다른 가격 체결,
중복/역순 전달마다 독립적인 비용/수량/예약을 검산하고 중간 재시작을 검사한다.
작은 유한 상태와 고정 시드로 현재 경계를 검증할 수 있어 Hypothesis 의존성은 추가하지 않았다.
전체 상태 공간을 탐색한 것은 아니다. 실제 병렬 writer 경합은 순서 고정 검증과 구분돼 있다.

## 재현·완성도 판단

대표 명령은 `python examples/fix_order_recovery.py`. 실제 child SIGKILL과 부모 venue 보존,
불완전 조회의 Incident, archive 재전달과 새 조회, 최종 독립 검산을 기존 예제로 재사용한다.
업무 시각/가격/수량은 고정이고 난수가 없다. UUID/PID는 소유권 진단용으로 실행마다 달라도
회계와 승인 판단에 영향을 주지 않는다. 필요한 Trade ID가 없으면 NORMAL을 강행하지 않는다.

제출용 학습/설계 포트폴리오로는 실행·검산·한계가 드러나는 상태다. 운영 배포용 OMS로는
부족하다. 실제 거래소 방언·FIX 세션/wire·권한/분산 fencing·체결 정정/수수료·계좌 전체 대사·
전원 손실·로그 순환·DB 이관이 없고 전체 재생 비용도 크다. 다음 개선 후보는 더 많은 기능보다
검증 계약을 보존하는 prefix/checkpoint 설계와 DB 이관이다. 구현하지 않은 최적화 성과는 없다.

## 현재 코드 성능 감사

재현: `python scripts/benchmark_audit.py --output /tmp/audit.json`.
[원시값·분산·환경·코드 해시](../benchmarks/engineering-audit.json)에 모든 표본을 보존했다.
조건별 새 DB 20개, warmup 없음, 순차 실행이다. p99는 표본 부족으로 null이다.
p95는 20개 표본 중 19번째 값일 뿐 안정적인 꼬리 추정이 아니다. 아래 단위는 ms, 각 셀은 중앙값 ± 표본 표준편차다.

| 조건 | 메모리 주문 reducer | 단일 Trade 변환 | 신규 주문 영속화 | FIX 보고 반영 | FIX 대사 | Incident 기록 | Startup Recovery | 전체 검산 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| history10 | 0.037 ± 0.005 | 0.052 ± 0.006 | 1.592 ± 0.080 | 1.766 ± 0.089 | 2.380 ± 0.043 | 2.430 ± 0.031 | 7.104 ± 0.135 | 1.216 ± 0.020 |
| history50 | 0.037 ± 0.004 | 0.051 ± 0.003 | 9.159 ± 0.097 | 9.258 ± 0.135 | 10.188 ± 0.136 | 10.223 ± 0.147 | 30.642 ± 0.281 | 8.515 ± 0.124 |
| history150 | 0.039 ± 0.001 | 0.052 ± 0.001 | 49.512 ± 0.629 | 49.303 ± 0.528 | 50.541 ± 0.558 | 50.607 ± 0.685 | 152.671 ± 1.562 | 48.295 ± 0.494 |
| active5 | 0.038 ± 0.002 | 0.053 ± 0.003 | 17.051 ± 0.055 | 16.840 ± 0.271 | 18.322 ± 0.228 | 18.095 ± 0.224 | 54.800 ± 0.304 | 16.142 ± 0.108 |
| fills3 | 0.041 ± 0.003 | 0.057 ± 0.004 | 15.059 ± 0.102 | 14.806 ± 0.201 | 16.061 ± 0.223 | 15.989 ± 0.151 | 48.558 ± 1.103 | 14.328 ± 0.085 |
| duplicates | 0.041 ± 0.003 | 0.055 ± 0.004 | 17.850 ± 0.074 | 17.712 ± 0.138 | 19.346 ± 0.161 | 19.341 ± 0.218 | 58.601 ± 0.493 | 16.940 ± 0.065 |

history10/50/150은 활성1·주문당체결1·중복0으로 H만 변경한다.
active5는 H50/활성5/체결1, fills3는 H50/활성1/체결3, duplicates는 H50/활성1/체결1/중복명령20(40%)이다.
동일 H를 맞추기 위해 나머지는 거부된 신규 결정으로 채운다. 따라서 명령 종류 구성도 달라진다.
활성 수/체결 수/중복 비율을 각각 바꾸지만 모든 혼합요인을 제거한 인과 실험은 아니다.

verify와 submit의 선행 이력은 H, report는 H+1, reconcile은 H+2, incident는 H+3이다.
startup는 Incident 기록 뒤 clean close(H+5)에서 락·open 검산·recover 검산·session_start 검산/커밋을 포함한다.
프로세스 spawn/네트워크 연결은 포함하지 않는다. 상태 전이는 새 메모리 OrderManager에 단일 snapshot apply이며
자금 승인/예약 계층은 제외한다. FIX는 단일 Trade prefix 변환이며 SQLite와 venue 생성은 제외한다.
각 구간은 perf_counter_ns wall time이고 seeding은 제외한다. 다른 프로젝트 테스트/벤치마크를 병렬 실행하지 않았다.
OS background 부하·캐시·열 상태는 통제하지 않았다. 동일 DB를 반복해 같은 명령 ID만 중복 조회한 측정이 아니다.

환경: Python 3.13.15, macOS-26.6.2-arm64-arm-64bit-Mach-O, SQLite 3.53.4.
SQLite delete journal, synchronous=2(FULL), busy_timeout=5000ms, page_size=4096. 로컬 임시 파일시스템이다.
매 명령 전체를 재생하며 운영 submit의 deepcopy가 누적 로그를 복사하고 FIX 수신도 누적 prefix를 재검산한다.
따라서 비용을 O(H)라고 단정할 수 없다. action별 상태 크기에 따라 재생 비용이 증가하고, 후보 복사가
쌓이면 H에 대해 이차 항이 생길 수 있다. 이번 측정은 일반적인 점근 복잡도 증명은 아니다.
150개에서 verify 중앙값이 약48ms이고 쓰기 약49ms여서 fsync만을 병목으로 지목할 근거가 없다.

선택: 성능 코어를 최적화하지 않았다. 후속은 profiler로 복사/검산 구간 분해 → 불변 prefix 및 checkpoint
계약 → 외부 writer/변조 감지 → 구버전 DB 검증/이관 → 크래시 회귀와 동일 부하 전후 비교다.
검산을 없앤 메모리 캐시나 synchronous 완화는 적용하지 않는다. 이 값은 과거 약9ms 수치도, 실거래 지연도 아니다.
CI에는 환경 의존 시간 합격선을 추가하지 않았다.

## 최종 실행 증거

- 기존 `.venv/bin/python scripts/verify_project.py`: 637 passed, skipped=0, 예제19개 성공.
- 새 `/tmp/oms-final-engineering-audit` 환경 생성 후 `pip install '.[dev,historical]'` 비편집 설치.
- 해당 환경 Python으로 `scripts/verify_project.py`: 637 passed, skipped=0, 예제19개,
  로컬 링크101개, pip check 성공. Python3.13.15/pytest9.2.0/PyArrow25.0.1.
- 모듈 경로가 새 환경의 site-packages임을 확인했다. 공개 예제는 검증기가 저장소 밖 임시 cwd에서 실행했다.
- `git diff --check`: 성공. benchmark raw120행과 현재 코어 SHA256 전체가 일치함을 별도 assertion으로 확인했다.
- 원격 CI, 새 Linux/Windows 실행, 실제 거래소·전원 손실은 이번 감사에서 검증하지 않았다.

이전 단계의 631개는 감사 전 기준이고, 현재는 결함 재현4개와 Incident/모델 검사2개를 추가한 결과다.
테스트/예제를 삭제하거나 기존 기대값을 완화하지 않았다. 표의 ±는 표본 표준편차이며 신뢰구간이 아니다.
