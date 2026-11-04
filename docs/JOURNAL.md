# 합성 주문 이벤트 저장과 원장 복구

기존 실행 엔진의 처리 완료 주문을 SQLite에 저장한다. 같은 이벤트가 다시 전달돼도
현금·수량을 두 번 반영하지 않고, 재열기 시 커밋한 이벤트로 원장을 재구성한다.

```bash
python examples/journal_recovery.py
python -m pytest -q tests/test_execution_journal.py
```

데모는 임시 디렉터리에 SQLite 파일을 만들고 종료 시 삭제한다. 기존의 연구·체결 시나리오
데모는 계속 결과 파일을 쓰지 않는다. 추가 패키지는 필요하지 않다.

## 이번에 선택한 범위

입력은 기존 `replay_execution`의 합성 bundle이다. 입력 전체를 고정하고 기존 엔진으로
정해진 주문 결과·체결·위험 검사 값을 계산한다. 이 결과가 저장 가능한 이벤트의 기준이다.
한 IOC 주문에 처리 완료 이벤트 하나, 선택적인 집계 체결 하나가 대응한다.
이벤트 ID는 order ID다. 실제 거래소의 여러 부분 체결 보고를 처리하는 프로토콜은 아니다.

호가 매칭과 원장 반영을 분리하기 위해 `execution_ledger.rebuild_account`를 추출했다.
기존 엔진의 최종 검산과 저널 복구가 같은 체결 원장 계산을 사용한다.
독립 손계산 테스트도 유지한다. 재생 일치는 회계 상태의 일관성 검증이지 체결 가정의 시장 검증이 아니다.

## API

저장소 루트에서 예제 입력을 사용하면 다음과 같이 실행할 수 있다.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from examples.execution_walkthrough import synthetic_inputs
from market_microstructure.execution_journal import ExecutionJournal

with TemporaryDirectory() as directory:
    path = Path(directory) / "orders.sqlite"
    with ExecutionJournal.create(path, synthetic_inputs()) as journal:
        events = journal.events()
        journal.record(events[0])
    with ExecutionJournal.open(path) as journal:
        print(journal.recover())
        print(journal.record(events[0])["status"])  # duplicate
        journal.record(events[1])
```

`events()`는 동결한 오프라인 실행에서 계산한 전체 이벤트의 복사본을 반환한다.
미래 주문까지 미리 아는 합성 시연이며 실시간 feed 소비 API가 아니다.
`record()`는 다음 이벤트만 새로 저장한다. 이미 저장한 이벤트의 재전달은 순서와 무관하게 확인한다.
`recover()`는 반영 이벤트 수와 현금·상품별 포지션·실현 손익·수수료·예약 담보를 반환한다.

## 저장과 중복 규칙

| 상황 | 동작 |
|---|---|
| 다음 이벤트, 내용 일치 | 이벤트와 반영 후 원장을 한 트랜잭션으로 커밋 |
| 이미 저장한 ID, 내용 일치 | `duplicate`, 원장과 이벤트 수 유지 |
| 이미 저장한 ID, 내용 상충 | 오류, 상태 유지 |
| 순서 누락·미등록·내용 변경 | 오류, 상태 유지 |
| 체결이 없는 거부·검열 결과 | 이벤트는 저장하되 체결 원장은 변경하지 않음 |
| 이벤트 삽입 후 커밋 전 종료 | SQLite가 미완료 트랜잭션을 복구, 이벤트와 원장 모두 미반영 |
| 커밋 후 응답 유실에 해당하는 재전달 | 저장된 ID 확인 후 중복 처리 |

SQLite의 `BEGIN IMMEDIATE`, 이벤트 ID 고유 제약과 `synchronous=FULL`을 사용한다.
각 기록 시 최신 커밋 상태를 다시 읽으므로 다른 연결의 커밋을 오래된 메모리 상태로 덮어쓰지 않는다.
실제 전원 차단·네트워크 파일시스템·장기 동시 부하의 내구성을 시험한 것은 아니다.

## 복구 검증

입력 JSON과 그 SHA-256, 실행·계약·위험·원장·저널 소스의 해시를 저장한다.
재열기 시 코드와 입력을 확인하고, 저장 이벤트가 기준 실행의 연속된 앞부분인지 검사한다.
선택적 위험 한도 설정도 동결 입력과 그 해시에 포함되며 한도로 거부된 이벤트도 저장한다.
그 체결 내역으로 원장을 다시 계산해 저장 원장과 대조한다. 불일치는 자동으로 덮어쓰지 않는다.

기존 경로는 새 실행으로 덮어쓰지 않으며, 복구에서 존재하지 않는 DB를 새로 만들지 않는다.
실패한 초기화 파일도 자동 삭제하지 않는다. 다른 코드 버전으로의 자동 이관은 지원하지 않는다.
해시는 로컬 재현 검사용이며 외부 인증이나 악의적으로 전체 DB를 다시 작성한 경우의 진위 증명은 아니다.

정상 단일 상품 예제는 진입 후 현금 `999.89900000`을 복구한다.
같은 진입을 다시 전달해도 금액이 유지되며, 청산 후 현금은 연속 실행과 같은 `1015.79000000`이다.
테스트는 공유 담보 예제의 모든 주문 경계, 중복·상충·순서 누락·원장 변경,
커밋 전/후 별도 프로세스의 실제 강제 종료도 확인한다.

## 아직 포함하지 않은 것

현재 원장을 처음부터 재생한다. 기록할 때도 전체 커밋 prefix를 검사하므로 작은 오프라인 예제용이다.
체크포인트나 고속 점진 처리는 구현하지 않았다. 복구 원장에는 가격 평가 equity가 없다.
현금·수량·원가를 복원하며 실시간 호가 잔량·대기 주문·미응답 상태는 복원하지 않는다.
거래소 ACK/취소/정정, 실제 체결 ingest, 주문 재전송·대사와 운영 OMS의 end-to-end exactly-once는 별도 과제다.
