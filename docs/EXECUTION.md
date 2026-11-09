# 실행 리플레이 — 단일 상품과 공유 담보

청산 노출 연구와 별도로, 주문이 도착한 시점의 표시 호가를 사용해 체결과 원장을 검증한다.
원본 Liquidation_gravity의 실행 연구에서 시간 선택·깊이 소진·검사 후 반영 원칙을 가져와
작은 모듈로 재구성했다. 단일 상품에서 시작해 여러 상품의 공통 담보 검사로 확장했다.
실제 주문을 전송하지 않으며 LGI를 주문 신호로 사용하지 않는다.

## 실행과 읽기 순서

기존 README의 패키지 설치 후 저장소 루트에서 실행한다. 추가 실행 의존성은 없다.

```bash
python examples/execution_walkthrough.py
python examples/portfolio_execution.py
python examples/execution_scenarios.py
python examples/risk_monitoring.py
python examples/risk_limits.py
python -m pytest -q tests/test_execution.py tests/test_portfolio_execution.py
```

1. `examples/execution_walkthrough.py`의 입력과 손계산을 읽는다.
2. `src/market_microstructure/execution_contracts.py`에서 입력 계약을 확인한다.
3. `src/market_microstructure/execution.py`에서 이벤트 정렬 → 체결 초안 → 검사 → 반영을 읽는다.
4. `src/market_microstructure/execution_risk.py`에서 계정 평가와 진입 검사를 읽는다.
5. 두 실행 테스트 파일에서 미래 호가, 부분 체결, 공유 담보와 원장 검산을 확인한다.

`execution_scenarios.py`는 정상 체결, 부분 체결, 공유 담보, 보유 가격의 유효기간 초과,
다른 상품의 평가가 불가능할 때의 수량 감소 청산을 한 번에 출력한다.
각 시나리오는 새 입력·계정·호가 잔량으로 시작한다. 자세한 손계산은 아래 설명을 참고한다.

## 예제의 정답

초기 담보 현금은 1,000이다. 시각 단위는 정수 나노초이며 예제의 1초는 `10**9`다.

| 주문 | 판단 | 도착 | 사용 호가 | 체결 |
|---|---:|---:|---|---|
| long 진입 | 0초 | 1초 | q0 | ask 101에서 2개 |
| long 청산 | 3초 | 4초 | q3 | bid 109에서 2개 |

```text
실현 총손익 = 2 × (109 - 101) = 16
수수료 = (202 + 218) × 5 / 10,000 = 0.21
최종 현금 = 1,000 + 16 - 0.21 = 1,015.79
최종 보유 수량 = 0
```

스프레드는 이미 bid/ask 체결가에 반영된다. 별도 비용으로 다시 차감하지 않는다.
예제의 가격·수량·수수료·tick/lot은 모두 사람이 정한 값이다. 거래소의 현재 사양이 아니다.

## 입력과 처리 규칙

API는 `from market_microstructure.execution import replay_execution`이며 예제의 dict를 받는다.
입력 전체를 먼저 검사한다. `source_kind`는 `SYNTHETIC_TEST_ONLY`만 허용한다.
기존 `instrument` 입력은 단일 상품이다. 여러 상품은 `instruments` 목록을 주고 각 quote/order에
등록된 `symbol`을 명시한다. 두 형식을 섞지 않는다. 최대 100개 상품이며 상품별 보유 포지션은 하나다.
모든 상품은 동일 담보·손익 통화와 계약 승수 1을 사용하는 합성 선형 계약이다.
교차 통화 환산이나 거래소 간 담보 이전을 가정하지 않는다. 가격·수량·금액은 Decimal 문자열이다.
유한한 24자리 이하, 지수 절댓값 12 이하의 값을 받는다. 시간은 bool을 제외한 63비트 비음수 정수다.

- 주문은 `signal_available_ns <= decision_ns`, 도착은 `decision_ns + latency_ns`다.
- 호가는 `timestamp_ns <= available_ns`이며 도착 시점까지 받은 것만 사용한다.
- 동일 시각은 호가 → 청산 → 진입 순서다. 같은 종류는 ID 사전순으로 처리한다.
  이는 고정된 시뮬레이션 규칙이며 실제 거래소의 순서를 추정한 것이 아니다.
- 최신 호가가 invalid/stale이거나 session이 다르면 이전 정상 호가를 되살리지 않는다.
- event 시각이 더 오래된 지연 호가는 해당 상품의 최신 호가를 덮어쓰지 않는다.
  같은 상품의 같은 event 시각은 입력 오류다. 서로 다른 상품의 같은 시각은 허용한다.
  quote ID와 order ID는 각각 입력 전체에서 고유해야 한다.
- age와 만료 시각은 경계를 포함한다. 리플레이 종료 후 도착하는 주문은 검열한다.
- 매수는 asks, 매도는 bids를 순서대로 소진한다. 각 새 호가는 전체 snapshot 교체다.
  같은 snapshot의 남은 깊이는 주문들이 공유한다. 새 snapshot을 실제 호가 재보충의 증거로 해석하지 않는다.
- 체결 초안을 계산하고 최소 진입 금액·담보를 확인한 뒤에만 깊이와 원장을 변경한다.
- 진입은 long/short 모두 지원한다. 이미 보유한 상품의 추가 진입은 거부하며
  청산은 해당 상품의 실제 보유 수량까지만 허용한다.
- IOC의 미체결 수량은 즉시 취소된 것으로 처리한다. `partial`은 종료된 부분 체결 결과다.
  남은 수량은 이후 호가를 기다리거나 재시도하지 않는다.

## 원장과 정밀도

기초자산 1단위당 quote 통화 손익이 선형으로 발생하는 단순 계약을 가정한다.
진입 notional을 현금에서 매수대금으로 차감하지 않고 담보 예약 금액으로 관리한다.
모든 상품이 현금 하나를 공유한다. 진입 후 수수료와 mid 평가 손실까지 반영한
계정 equity가 전체 예약 담보 및 전체 gross 노출을 각각 충당해야 한다.
유지 증거금·강제청산은 구현하지 않았다.

내부 계산은 로컬 Decimal precision 128과 HALF_EVEN을 사용한다.
현금 수수료·실현 손익은 체결마다 1e-8 단위로 HALF_EVEN 반올림한다.
부분 청산은 진입 원가를 비례 해제하므로 분할 체결의 반올림 결과가 일괄 청산과 다를 수 있다.
호출자의 Decimal precision과 rounding 설정은 변경하지 않는다.

`fills`는 금액·수량·수수료·실현 손익·체결 후 현금을 남긴다.
반환 전 체결 내역으로 원가·수량·손익·수수료·현금을 다시 구성하고 엔진 집계와 대조한다.
이 검산 외에 독립 손계산 기대값도 테스트한다. 입력 dict와 중첩 데이터는 수정하지 않는다.

미청산 포지션은 `terminal_flat=false`와 실제 잔여 수량을 남긴다.
종료 시 유효한 동일 session의 가격이 없으면 미실현 손익·equity는 null이다.
현금 변화는 미청산 손익을 포함하지 않는다. 남은 포지션을 임의 가격으로 닫지 않는다.
손익 값은 합성 원장 계산이며 전략 성과가 아니다.

## 공유 담보 검사와 결과 읽기

```text
equity = 현금 + 보유 상품 전체의 현재 미실현 손익
reserved_margin = 보유 상품의 남은 진입 원가 합계
gross_notional = 보유 수량 × 현재 mid의 합계 (long/short 상계 없음)
required_collateral = max(reserved_margin, gross_notional)
collateral_headroom = equity - required_collateral
```

진입 초안에는 실제 체결 가능 수량·notional·수수료와 즉시 발생하는 mid 평가 손익을 반영한다.
`collateral_headroom >= 0`이면 승인한다. `free_collateral=equity-reserved_margin`도 제공하지만,
gross가 더 큰 경우 이것만으로 승인하지 않는다. 비용이 없다는 가정도 정책에 명시해야 한다.
같은 시각의 경쟁 주문은 고정한 ID 순서대로 계정 상태를 갱신한다. 동시 예약이나 최적 배분은 아니다.

각 주문의 `risk`에는 다음이 들어간다.

| 필드 | 의미 |
|---|---|
| `before` | 판단에 사용한 계정 현금·평가손익·담보·노출과 상품별 가격 근거 |
| `candidate` | 체결 초안의 수량·notional·수수료·mid |
| `projected` | 신규 진입 후 예상 계정 수치와 담보 여유 |
| `after` | 체결 반영 후 계정 수치. 거부하면 null |
| `status` | `approved`, `rejected`, `reduce_only`, 또는 `not_evaluated` |
| `reason` | 담보 부족·평가 불가 또는 위험 검사 전 중단한 이유 |

만료·대상 호가 오류 등으로 진입 검사에 도달하지 않으면 `not_evaluated`다.
리플레이 끝 이후 도착한 주문은 검열하며 `risk=null`이다.
미청산 상품 하나라도 stale/invalid/session 변경이면 계정 equity·gross·headroom은 null이고
`unavailable_symbols`와 `marks`에 이유를 남긴다. 알고 있는 현금·예약 담보는 유지한다.
이 경우 신규 진입은 `portfolio_mark_unavailable`로 거부한다. 보유하지 않은 상품의 가격 부재는
계정 평가를 막지 않는다. 대상 상품의 유효한 가격이 있으면 다른 상품의 평가가 불가능해도
수량을 줄이는 청산을 허용한다. `reduce_only`는 계정 전체가 건전하다는 인증이 아니다.

포트폴리오 예제는 초기 현금 1,000과 수수료 0으로 다음 과정을 보여준다.

| 처리 | 핵심 수치 | 결과 |
|---|---|---|
| BTC long 5개를 101에 진입 | mid 100, equity 995, 예약 505 | 승인 |
| ETH long 50개를 11에 진입 시도 | mid 10, 예상 equity 945, 필요 담보 1,055 | 110 부족으로 거부 |
| BTC 2개를 109에 청산 | 현금 1,016, 남은 BTC 원가 303, equity 1,043 | 승인 |
| ETH long 50개 재시도 | 예상 equity 993, 필요 담보 853 | 140 여유로 승인 |

최종 보유 수량은 BTC 3개, ETH 50개다. 최종 cash=1,016, equity=993, 예약=853, gross=830이다.
거부된 주문은 비용·포지션·깊이에 반영하지 않는다. 재시도는 예제에 미리 명시한 별도 주문이다.
엔진이 거부된 주문을 자동 재시도하지 않는다.

결과 schema는 `synthetic-execution-result-v2`다. `terminal_positions`는 미청산 상품별 상태,
`terminal_account`는 전체 계정 상태다. 기존 단일 입력에는 `instrument`와 `terminal_position`도
계속 제공한다. v1 출력과 schema·추가 필드는 다르지만 기존 단일 상품의 손계산 결과는 유지한다.
원장 검산은 상품별 방향·session·수량·원가와 계정 전체 현금·수수료·실현 손익·예약 담보를 대조한다.

## 현재 한계와 다음 검토

펀딩·추가 slippage/impact·레버리지·보유 중 강제 위험 조치·주문 취소 요청/응답·정정·
거래소 ACK·재연결 대사·실시간 미응답 주문과 호가 상태 복구는 구현하지 않았다.
표시된 깊이를 사용하며 queue, hidden liquidity, 시장 피드백은 모델링하지 않는다.
원본의 완전한 실행 엔진이나 운영 OMS를 이식한 것이 아니다.
체결 엔진의 위험 검사는 주문 처리 전후와 종료 시점에 계산한다.
아래 별도 관측기는 호가·체결·만료 경로를 재생하며 자동 강제청산을 수행하지 않는다.

합성 실행의 처리 완료 이벤트 저장·중복 처리·현금/포지션 원장 복구는 별도 모듈로 구현했다.
계약과 사용법은 [JOURNAL.md](JOURNAL.md)를 참고한다. 실시간 주문 상태 복구와는 범위가 다르다.
현재 OMS 검증은 [운영 근거](OPERATIONAL_EVIDENCE.md)에 있다. 이전 개발 순서와 검증 기록은
[고정된 역사적 계획](https://github.com/Min9s/order-management-system-simulator/blob/541cbc34932498a7ca97e2ad94f681786e4fcd47/docs/development/PLAN.md)에 보존한다.

## 보유 중 위험 관측

```python
from market_microstructure.execution_monitor import monitor_execution

observed = monitor_execution(bundle)  # 기존 replay_execution과 같은 합성 입력
execution = observed["execution"]    # 기존 실행 결과와 동일
events = observed["risk_events"]
```

입력은 기존 계약으로 검증한다. 반환 schema는 `synthetic-risk-monitor-v1`이며 기존 실행의
v2 결과를 `execution`에 포함한다. 기존 실행 API·체결·현금·포지션에는 새 필드를 넣거나 조치를 가하지 않는다.
모니터링은 실제 모의 체결로 원장을 재구성하고 당시 수신한 호가만 사용한다.

| 필드 | 의미 |
|---|---|
| `observations` | 시작, 수신 호가, 실제 체결, 현재 호가 만료, 종료의 계정 상태와 원인 |
| `risk_events` | 위험의 `started`, 위험 종류/평가 불가 사유의 `changed`, 정상 복귀 `resolved` |
| `episodes` | 문제 관측 구간의 시작·해소와 관측 길이. 미해소면 end_ns=null/open_at_end=true |
| `terminal_status` | 종료 시점의 관측 상태 |
| `automatic_actions` | 항상 false |

상태는 `healthy`, `collateral_breach`, `valuation_unavailable`, `exposure_limit_breach`이다.
headroom이 음수일 때 담보 한도 위반이며 정확히0이면 정상이다.
보유 가격 하나라도 부적격하면 equity·headroom을 null로 남기고 평가 불가로 분류한다.
이 상태를 담보 한도 충족이나 회복으로 해석하지 않는다. 보유하지 않은 상품의 만료는 위험이 아니다.

동일 시각은 시작 → 호가(ID순) → 만료 → 청산(ID순) → 진입(ID순) → 종료 순이다.
원래 실행의 호가/청산/진입 순서를 유지한다. 원자적 반영마다 기록하므로 같은 시각의
악화·청산으로 길이가0인 문제 구간이 생겨도 두 사건을 숨기지 않는다. observation sequence로 순서를 구분한다.
늦게 온 오래된 호가는 무시한다. 새 호가로 교체된 옛 호가의 만료 타이머도 무시한다.

나이 경계는 포함이므로 `timestamp + max_quote_age_ns + 1`부터 만료다.
새 호가가 그 시각에 도착하면 호가를 먼저 반영해 잘못된 평가 불가 전환을 만들지 않는다.
종료 이후 만료는 기록하지 않으며 종료 정확히 만료면 미해소 상태로 남긴다.

예제에서 초기 현금204·long2·진입101·수수료0은 mid100에서 headroom0으로 승인된다.
3초에 mid50이면 equity102/예약202/headroom-100, 4초에 mid100이면 다시 headroom0이다.
다른 예제는 1.000000001초부터 4초까지의 stale 구간을 기록한다.

구간은 담보 부족과 평가 불가를 모두 포함할 수 있다. `observed_duration_ns`는 그 합성 경로의
문제 관측 길이이며 실제 시장의 지속적인 담보 부족 시간이나 독립 사건 수가 아니다.
장중 가격을 보간하지 않으며 감시·경보 서버, 강제청산, journal의 감시 상태 저장/복구는 제공하지 않는다.
현재 구현은 체결 prefix를 반복 재생하므로 작은 오프라인 사례에 맞춘 방식이며 처리량을 검증하지 않았다.

## 선택적인 위험 한도

기존 `policy`에 아래 `risk_limits`를 추가하면 담보 조건과 별도로 한도를 검사한다.
필드 전체를 생략하면 기존 동작을 유지한다. 명시할 때는 세 항목 모두 필요하며,
상품 한도에는 등록된 모든 symbol이 있어야 한다. 값은 양의 Decimal 문자열이다.

```python
bundle["policy"]["risk_limits"] = {
    "max_order_notional": "500",
    "max_symbol_gross": {"SYNTH-BTC-USDT": "250"},
    "max_portfolio_gross": "600",
}
```

| 한도 | 진입 검사 값 | 보유 중 관측 |
|---|---|---|
| `max_order_notional` | 요청 수량×당시 반대편 최우선 호가와 체결 초안 금액 중 큰 값 | 주문 시점에만 검사 |
| `max_symbol_gross` | 모든 보유 상품 및 신규 상품의 체결 후 수량×mid | 상품별 현재 수량×유효 mid |
| `max_portfolio_gross` | 보유 상품 전체의 진입 후 gross 합계 | 전체 gross 합계, long/short 상계 없음 |

주문 한도는 작은 표시 깊이로 요청 금액을 숨기지 않게 하고, 깊은 가격에서의 큰 체결 초안도
포괄하는 참조 금액 규칙이다. 미체결 수량의 실제 체결가를 추정하거나 모든 가능한 시장 가격의
상한을 보장하지 않는다. 상품·계정 한도는 실제 체결 가능한 수량으로 계산한다.
단위는 모든 상품이 공유하는 담보/손익 통화다. 한도와 같으면 허용하고 초과하면 거부한다.

진입의 `risk.projected.limit_checks`에서 name/symbol/value/limit/headroom/passed를 확인한다.
계정의 현재 상품·계정 한도 검사는 `limit_checks`, 현재 상품별 노출은 `symbol_gross`에 기록한다.
설정을 생략한 입력에는 이 추가 필드를 만들지 않는다.
예를 들어 기본 long2 진입은 요청 참조금액202, mid 노출200이다.
상품 한도를180으로 두면 담보 여유가 양수여도 `max_symbol_gross_exceeded`로 거부한다.

발생 시각·수신·만료·호가·포지션 정합성·최소금액 검사를 먼저 수행한다.
전체 계정 평가가 불가능하면 기존처럼 `portfolio_mark_unavailable`로 거부한다.
그 다음 기존 담보 위반을 우선 보고하고, 담보가 충족되면 주문 → 상품(symbol순) → 계정 한도의
첫 위반을 사유로 사용한다. 계산한 한도 검사 내역은 모두 남긴다.
거부된 주문은 체결·비용·호가 잔량을 변경하지 않는다. 이미 다른 보유 상품이 한도를 초과했다면
그 상태도 신규 진입을 막는다.

대상 호가와 포지션이 적격한 청산은 `reduce_only`로 신규 진입 한도에서 제외한다.
요청 수량이 보유 수량을 넘어도 실제 수량까지만 청산하며, 초과 요청을 반대 포지션 진입으로 바꾸지 않는다.
청산 후에도 남은 한도 위반은 계정 수치와 모니터에 남는다.

모니터는 담보가 충분해도 상품·계정 한도가 초과되면 `exposure_limit_breach`를 기록한다.
여러 한도가 동시에 위반되거나 위반 항목이 바뀌면 검사 내역과 변경 이벤트로 확인한다.
가격 평가 불가를 최우선 상태, 담보 부족을 그 다음 상태로 표시하되 알려진 한도 위반은 숨기지 않는다.
다른 상품 가격이 없어 전체 노출을 모르면 해당 검사 값·여유·passed는 null이며 통과로 취급하지 않는다.
위험 한도 초과로 자동 주문·강제청산하지 않는다.
