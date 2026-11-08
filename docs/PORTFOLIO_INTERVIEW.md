# Resume and interview material

## Project section

**Synthetic OMS — Recovery, Reconciliation & FIX Business Fields**

A Python OMS simulator for testing order/account consistency under lost responses, duplicate reports, and process crashes.

**Stack:** Python 3.11+, Decimal, SQLite, POSIX flock, pytest, FIX 4.4 business-field objects, GitHub Actions.

- Built a cash-funded OMS simulator with atomic SQLite journaling, cancel-fill race handling, and execution-ID-based idempotency.
- Verified process-crash recovery and reconciliation against an independent synthetic venue ledger, including startup risk gates and persistent incident tracking.
- Implemented a restricted FIX 4.4 business-field adapter with missing-trade buffering and adversarial checks; documented measured replay costs and unsupported session features.

**GitHub About:** Synthetic Python OMS: order accounting, crash recovery, reconciliation, incident controls, and a restricted FIX 4.4 field adapter.

## 30-second introduction

I built a Python OMS simulator to study what happens when order responses are lost or arrive out of order. It keeps cash reservations until authoritative execution and cancellation facts are confirmed, and journals account changes atomically in SQLite. The demo kills the client process, restarts in recovery mode, and reconciles with a surviving synthetic venue. I also added a limited FIX business-field adapter. It is a correctness and recovery project, not a live trading system or a complete FIX engine.

## Two-minute technical explanation

The central problem is that a failed connection does not tell us whether an order reached the venue. Releasing its reserved cash or resending it immediately could create unintended exposure. My simulator records admission and reservations before returning an outgoing command, and treats an unanswered order as unresolved.

The order reducer keeps cancellation requests separate from order status. If four units fill, a cancel is requested, and three more units fill before cancellation completes, the final result is seven filled and three canceled. Cumulative reports are checked against immutable execution IDs and revisions. Old valid reports cannot roll back newer facts, and conflicting facts are rejected.

SQLite is the durable source of truth. Commands, results, and snapshots are committed together. Recovery replays the complete history and compares it with the stored snapshot. That makes correctness review straightforward, but it is expensive as history grows. I measured the current implementation rather than applying historical latency numbers to the expanded system.

The execution entry point uses a POSIX file lock for cooperating processes. Every new session starts in recovery, even after a clean shutdown. Unresolved incidents or missing query evidence prevent normal risk admission. Existing reports and necessary cleanup remain available.

The FIX adapter handles a small field-level profile. A Trade report contains the last execution, while my domain needs cumulative execution facts. If earlier execution IDs are missing, I persist the report and wait for actual redelivery; I do not infer executions from average price. The end-to-end demo uses a real SIGKILL and an independent venue ledger. It does not implement FIX wire framing, sessions, live connectivity, fees, or execution corrections.

## 한국어 면접 답변과 근거

1. **왜 개발했는가?** 연구 신호의 수익성과 별개로 주문 전송 결과가 불명확할 때 자금과 체결 사실을 어떻게 보존하는지 직접 검증하려고 개발했다. 개인 동기는 본인의 실제 경험에 맞춰 설명해야 한다. 구현 근거는 [대표 장애 흐름](../examples/fix_order_recovery.py)이다.
2. **일반 주문 프로그램과 OMS의 차이?** 전송 API 호출 이후에도 주문 식별, 잔여 예약, 취소/체결 경합, 미수신 사실과 재시작 상태를 관리한다. [OrderManager](../src/market_microstructure/order_lifecycle.py)의 submit/request_cancel/apply와 [예약 계산](../src/market_microstructure/order_reservations.py)의 account를 예로 들겠다. 상용 OMS의 전체 기능을 구현했다는 뜻은 아니다.
3. **취소와 체결이 동시에 발생하면?** 취소 요청은 pending으로 두고 체결을 계속 반영한다. 종료 보고가 확인돼야 잔여 예약을 해제한다. [request_cancel/apply](../src/market_microstructure/order_lifecycle.py), [전달 순서 검증](../tests/test_cancel_fill_race.py)을 보여준다.
4. **중복 보고는?** command ID, event ID, execution ID를 구별한다. 같은 체결 ID의 동일 사실은 한 번만 합산하고 변경된 사실은 거부한다. FIX의 비체결 ExecID를 체결로 취급하지 않는다. [apply](../src/market_microstructure/order_lifecycle.py), [reduce_fix](../src/market_microstructure/fix_adapter.py)가 근거다.
5. **자금은 언제 해제하는가?** 체결 대금은 현금에서 차감하고 비종료 잔량만 예약한다. 취소 요청이나 조회 부재만으로 해제하지 않는다. 취소/거부/전량 체결 같은 검증된 종료 사실이 필요하다. [account](../src/market_microstructure/order_reservations.py), [미응답 회귀](../tests/test_funded_journal.py)가 근거다.
6. **왜 SQLite인가?** 한 로컬 DB에서 명령·결과·스냅샷의 원자적 커밋과 프로세스 종료 실험을 작게 재현할 수 있어서다. FULL synchronous도 전원 손실 검증을 대신하지 않는다. 전체 재생과 단일 writer 비용은 감수했다. [_record/_verify](../src/market_microstructure/funded_journal.py)를 설명한다.
7. **FIX와 도메인을 왜 분리했는가?** FIX의 delta Trade와 내부 누적 체결 모델이 다르고, 세션 sequence를 도메인 revision으로 쓰면 안 되기 때문이다. 기존 회계 reducer를 유지하며 필드 검증과 누락 prefix 처리를 어댑터에서 수행한다. [decode_history/reduce_fix](../src/market_microstructure/fix_adapter.py)가 근거다.
8. **FIX 세션과 비즈니스 메시지 차이?** 세션은 Logon, Heartbeat, sequence, Resend/Gap Fill 같은 전달 상태를 다루고 비즈니스는 주문·체결·취소의 의미를 다룬다. 여기에는 태그/값 객체만 있고 세션과 바이트 인코딩은 없다. [지원 범위](FIX_ADAPTER.md)와 [SyntheticFixVenue](../src/market_microstructure/synthetic_fix.py)를 보여준다.
9. **실제 OMS와 가장 큰 차이?** 실제 거래소/계좌의 사실 취득, 세션 신뢰성, 권한·소유권 강제, 정정·수수료·운영 환경 검증이 없다. 파일 락도 협력 API 범위다. [TradingSession](../src/market_microstructure/trading_session.py), [알려진 제약](ENGINEERING_AUDIT.md)이 근거다.
10. **가장 개선하고 싶은 부분?** 전체 재생 비용과 DB 이관이다. 단순 캐시로 검산을 없애기보다 검증된 prefix/checkpoint와 변경 탐지·크래시 계약을 먼저 설계하겠다. 현재는 구현하지 않았다. [현재 측정 스크립트](../scripts/benchmark_audit.py), [_verify](../src/market_microstructure/funded_journal.py)를 연결해서 설명한다.

이 자료는 구현 근거를 정리한 초안이다. 본인이 직접 설계·구현한 범위와 AI 보조 범위를 구분해서 말하고,
실거래 운영·FIX 인증·HFT 성능 또는 개인 경력을 추가로 주장하지 않는다.
