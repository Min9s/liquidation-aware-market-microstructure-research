# OMS v2 / 패키지 0.2.0 릴리스 준비

상태: **기능 브랜치의 릴리스 후보. 원격 Linux CI 확인 전 공개 릴리스 보류.**
검토일은 2026-11-05다. 이번 작업에서 main 병합·push·태그·GitHub Release 생성은 하지 않는다.
로컬/공개 main 기준은 `fb7afec7909d07cdc41d9a3f899361c982e06f0d`이고 준비 브랜치는 `feat/simulated-order-lifecycle`이다.
패키지 버전은0.2.0으로 준비하며 실제 버전 태그는 만들지 않았다.

## main 대비 포함할 변경

| 영역 | 공개 v1 | v2 후보 |
|---|---|---|
| 주문 처리 | 동결된 IOC 완료 이벤트 | 별도 주문/취소 상태, 부분 체결, 누적 보고, 중복·역순 응답 |
| 회계 | 합성 선형 계약의 담보·손익 | 별도 현금 계정의 매수 현금·매도 수량 예약, 공유 자금 검사 |
| 복구 | 완료 이벤트와 파생계약 계정 재생 | 발송 전 명령·예약 저장, 미종료 주문 조회, 유실 체결 복원 |
| 장애 검증 | v1 저널 강제 종료 테스트 | 실제 SIGKILL E2E와 독립적인 가상 거래소 원장 검산 |
| 실행 계약 | 오프라인 재생 | 생성 스레드 소유권, 접수 예산 이후 기존 주문 정리 허용 |
| 공개 재현성 | 한국어 사례와 통합 검증 명령 | v2 중심 README, 단계별 대표 출력, Python 3.11/3.13 Linux workflow |

새 코어는 order_protocol/order_lifecycle/order_reservations/funded_orders/funded_journal/simulated_venue의6개 모듈이다.
기존 v1 연구·실행 코드는 변경하지 않았다. v1 담보와 v2 현금 계정은 합치지 않는다.
전략·실제 거래소 연결·새 UI·C++는 추가하지 않았다.

통합 전 diff는 다음 읽기 전용 명령으로 확인한다.

```bash
git log --oneline main..feat/simulated-order-lifecycle
git diff --stat main...feat/simulated-order-lifecycle
git diff --check main...feat/simulated-order-lifecycle
```

## 최종 코드 검토 범위

이번 준비 작업에서는 추가로 수정할 P0/P1 오류를 재현하지 못했다. 아래 계약을 코드와 기존 회귀에서 확인했다.
이것은 모든 오류가 없다는 보장이 아니며 v2 엔진을 다시 구현하거나 공통 추상화를 추가하지 않는다.

| 불변조건 | 구현·실패 검증 근거 |
|---|---|
| 취소 요청만으로 종료하지 않음, 취소 중 체결 반영 | [상태 처리](../../src/market_microstructure/order_lifecycle.py), [전달 순서·경쟁 테스트](../../tests/test_order_lifecycle.py) |
| 체결 ID별 불변 사실과 관측 revision 충돌 거부 | [프로토콜](../../src/market_microstructure/order_protocol.py), [과거 revision·소유권 회귀](../../tests/test_order_safety.py) |
| 확인 전 잔량 예약 유지, 현금/수량 중복 사용 방지 | [예약 계산](../../src/market_microstructure/order_reservations.py), [자금·수량·시세 경계](../../tests/test_funded_orders.py) |
| 후보 전체 검증 뒤에만 메모리 상태 반영 | [승인 계층](../../src/market_microstructure/funded_orders.py), [변조 보고의 무변경 거부](../../tests/test_funded_orders.py) |
| 명령·계정·예약을 같은 트랜잭션에 저장하고 커밋 뒤 outgoing 반환 | [저널](../../src/market_microstructure/funded_journal.py), [커밋 전후 종료·롤백 테스트](../../tests/test_funded_journal.py) |
| 재시작 시 미발송을 추정하지 않고 조회 부재에서도 예약 유지 | [저장 계약](../DURABLE_ORDERS.md), [SIGKILL E2E](../../tests/test_order_recovery_e2e.py) |
| 접수 중단 후에도 기존 주문의 체결·취소·대사 수용 | [메모리·SQLite 포화 회귀](../../tests/test_order_safety.py) |

확인한 제한: cancel_rejected는 종료 주문에 한정되며, 외부 수동 거래·체결 정정·계좌 전체 대사·세션 epoch 전환은 지원하지 않는다.
예제의 대사는 가상 거래소의 주문/체결 사실과 로컬 회계의 비교다. 실제 거래소 계좌 조회가 아니다.

## 검증 명령과 결과

```bash
python -m pip install '.[dev,historical]'
python scripts/verify_project.py
python examples/durable_order_recovery.py --crash
```

| 확인 대상 | 상태 |
|---|---|
| macOS Python3.11 / Python3.13 비편집 설치 | Python3.11.6·3.13.15의 별도 가상환경에0.2.0 설치 후 각각 전체 검증·대표 E2E 통과 |
| 전체 테스트·예제·문서·의존성 | 두 환경 각각589 passed/skip0·예제15개·로컬 링크77개·pip check 통과 |
| 실패·skip 차단 | 임시 복사본에 의도적인 실패/skip 테스트를 각각 주입. 두 경우 모두 검증 명령 exit1 확인 후 주입 파일 제거 |
| wheel·sdist 빌드/포함 | 0.2.0 빌드, workflow·릴리스 문서 포함 확인. sdist 추출본 전체 검증과 최소 wheel 환경의 SIGKILL 데모 통과 |
| Linux workflow 정적 검사 | 공식 배포본 체크섬을 확인한 actionlint1.7.12 통과. shellcheck/pyflakes 외부 연동은 제외 |
| GitHub-hosted Ubuntu24.04 / Python3.11·3.13 | **미실행**. 업로드 금지 조건으로 원격 실행을 시작하지 않음 |

로컬에는 Linux 컨테이너 런타임이 없다. macOS에서 Python 버전별 검증한 결과를 Linux 성공으로 표시하지 않는다.
실제 검증 의존성은 pytest9.2.0·PyArrow25.0.1이며 설치 허용 범위는 pyproject.toml에 선언돼 있다.
실패/skip 주입과 배포 검증은 저장소 밖의 임시 복사본에서 수행했다. 주입 테스트와 local/·output/ 파일은 배포하지 않는다.

## CI가 수행할 일

[ci.yml](../../.github/workflows/ci.yml)은 push/pull_request/workflow_dispatch에서 두 matrix job을 실행한다.
각 job은 비편집 패키지와 dev/historical 의존성 설치, 기존 verify_project.py, 대표 --crash를 실행한다.
검증 스크립트는 JUnit의 failures/errors/skipped를 검사한다. step 실패를 무시하는 continue-on-error는 사용하지 않는다.
fail-fast=false는 한 버전의 실패가 다른 버전의 검증을 취소하지 않게 하며, 실패를 성공으로 바꾸지 않는다.
checkout/setup-python은 확인한 공식 v6 commit SHA에 고정하고 저장소 권한은 contents:read만 사용한다.

구성 근거는 [GitHub Python CI 문서](https://docs.github.com/en/actions/tutorials/build-and-test-code/python),
[setup-python matrix 설명](https://github.com/actions/setup-python/blob/main/docs/advanced-usage.md)이다.
GitHub Actions 자체를 이번 작업에서 실행하거나 변경한 원격 파일은 없다.

## 릴리스 판정과 이후 실행 순서

로컬 검증과 정적 검사가 끝나도 Linux matrix를 실행하기 전까지 릴리스 판정은 조건부다.
사용자가 원격 업로드를 별도로 요청한 뒤 아래 순서로 진행한다.

1. 준비 브랜치를 업로드해 정확한 커밋에서 Linux Python3.11/3.13 두 job을 실행한다.
2. 두 job의 패키지 설치·전체 검증·대표 E2E가 성공했는지 확인한다. 실패/skip이면 원인을 수정하고 다시 검증한다.
3. 승인된 diff와 결과를 바탕으로 main에 통합한다. main에서도 동일 CI가 성공해야 한다.
4. README/이 기록의 미실행 상태를 실제 결과로 갱신하고, 별도 요청에 따라0.2.0 태그·릴리스 여부를 결정한다.

현재 코드의 알려진 제약:

- 메모리 엔진은 단일 소유 스레드 전용이며 내장 콜백 큐가 없다.
- 신규 접수 중단선 뒤 정리 감사 기록은 계속 증가한다. 전체 재생 비용과 디스크 사용량을 제한하지 않는다.
- 미접수 미확정 주문은 예약이 남을 수 있다. 재시도·세션 fencing·exactly-once 전송을 제공하지 않는다.
- 코드 해시가 달라진 DB는 열기를 거부한다. 자동 이관·백업·전원 손실 시험은 없다.
- 강제 종료 데모는 POSIX 전용이다. 실제 거래소·수수료·차입·공매도·전략 수익성과 처리량/지연은 검증 범위 밖이다.
