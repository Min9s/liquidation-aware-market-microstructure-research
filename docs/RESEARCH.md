# 연구 계산과 재현 규칙

이 문서는 README에서 분리한 연구 기능의 상세 설명입니다.
처음 실행하려면 [README.md](../README.md), 함수별 학습은 [LEARNING.md](LEARNING.md)를 참고하세요.
실행·원장 처리는 [EXECUTION.md](EXECUTION.md)에 설명합니다.

## 연구 질문과 공개 범위

청산가가 현재 가격에 가까울수록 가중치를 높인 지표가 단순한 가격 이동 이상의 정보를
담는지 살펴보는 것이 연구의 출발점입니다. LGI는 가격 이동만으로도 바뀌며 새 계정 관측,
청산가 갱신, 결측과 오래된 상태의 제외도 계산에 영향을 줍니다.

공개 저장소는 합성 자료로 계산·자료 선택·결과 라벨·가격/상태 변화 분해를 재현합니다.
비공개 역사 연구의 입력·실험 스크립트·수치 결과는 포함하지 않습니다.
공개 예제 실행은 실제 시장의 예측력이나 인과적 효과를 재현하는 절차가 아닙니다.

## 기본 미시구조 지표

가격은 양의 유한수, 수량은 0 이상의 유한수여야 합니다. bool과 비유한 계산은 거부합니다.
호출 전에 단위와 계약 승수를 맞춰야 합니다. quote 함수는 시각 입력이 없으므로 신선도를 검사하지 않습니다.

| 함수 | 계산·반환 | 경계 |
|---|---|---|
| `bid_ask_spread(b,a)` | mid, 절대 차이, mid 대비 bps | `0 < b <= a`, locked book 허용 |
| `order_book_imbalance(qb,qa)` | `(qb-qa)/(qb+qa)` | 양쪽 0이면 `None` |
| `notional_depth(b,a,qb,qa)` | bid·ask·합계 명목금액 | 최우선 호가만 계산 |
| `trade_imbalance(rows, decision_ns=..., window_ns=...)` | buy·sell 금액, 불균형, 체결 수 | 금액 합계 0이면 불균형 `None` |

bid=99, ask=101, 수량=3/1이면 mid=100, spread=2=200bps, OBI=0.5,
표시 깊이는 297/101/398입니다. 양쪽 깊이 합계는 한 방향에서 체결할 수 있는 금액이 아닙니다.

체결 행에는 `timestamp`, `ingested_at`, `price`, `size`, `side`가 필요하며
side는 정확히 `buy` 또는 `sell`입니다. 발생 시각 창은 `(decision-window, decision]`,
수신 시각은 `<= decision`입니다. 창 밖의 행도 검증합니다. 단일 자산 선택과 중복 제거,
매수/매도 방향 정의는 호출자 책임입니다. 이 작은 도우미는 두 시간 필터를 적용하지만
clock inversion 자체는 거부하지 않습니다. as-of·역사 재생은 더 엄격한 시각 검사를 적용합니다.

## Gravity와 LGI

signed size를 q, 관측 청산가를 L, 기준 mid를 P라 하면 다음과 같습니다.

```text
notional = abs(q) * P
distance_bps = abs(L-P) / P * 10000
G_up   = sum(short notional * exp(-distance_bps / tau_bps))
G_down = sum(long  notional * exp(-distance_bps / tau_bps))
LGI = (G_up-G_down) / (G_up+G_down+epsilon)
```

`prepare_exposure`는 포지션 하나의 포함 여부·사유·방향·금액·거리를 반환합니다.
청산가 결측, 기준가와 같은 청산가, 포지션 방향과 맞지 않는 청산가는 제외하고 이유를 남깁니다.
기준가를 넘었다는 이유로 실제 청산을 발생시키지 않습니다.

`gravity`는 `(notional, distance_bps)` 쌍에 지수 감쇠를 적용합니다.
tau는 양수인 거리 가중 설정이며 추정한 청산 확률이 아닙니다.
`liquidation_imbalance`의 기본 epsilon은 `1e-12`입니다.
관측된 빈 노출의 0과 관측 자체가 없거나 모두 부적격한 상태는 역사 재생에서 구분합니다.
극단적인 LGI도 절대 노출 금액이 크다는 뜻은 아닙니다.

역사 재생은 Decimal로 가격 방향·거리 경계를 준비한 뒤 float로 감쇠·합산합니다.
단일 포지션 도우미는 float를 사용하므로 반올림 경계가 다를 수 있습니다.
기준가격 배율의 공통 효과는 epsilon을 제외하면 LGI에서 대체로 상쇄되지만 거리·포함 경계는 가격에 의존합니다.

## 시점별 자료 선택과 역사 재생

`select_asof`는 status, reason, record, record_id, age_ns를 반환합니다.

| 시각 | 필드 | 의미 |
|---|---|---|
| 발생·상태 시각 | `timestamp` | 자료에 기록된 상태의 시각 |
| 수신 시각 | `ingested_at` | 계산 시스템이 받은 시각 |
| 판단 시각 | `decision_ns` | 계산할 기준 시점 |

모두 비음수 정수 나노초입니다. 판단 시점까지 받은 최신 자료를 **먼저** 고르고,
그 상태의 발생 시각·clock inversion·완전성·나이를 검사합니다.
최신 자료가 부적격해도 이전 정상 자료를 되살리지 않습니다. 최신 수신의 상충 기록도 이용 불가입니다.
`timestamp_basis=received`이면 timestamp와 수신 시각이 같아야 합니다.
이는 독립적으로 측정한 거래소 시계가 아닙니다. complete·출처 표시는 입력 계약이지 사실 인증이 아닙니다.

`replay_history(data, policy)`는 BTC 전용 어댑터입니다. 계정 헤더와 모든 자산의 자식 포지션,
BTC context·BBO를 받고 스냅샷 ID·계정 일치·자식 수·자식 시각·중복 자산·session·신선도를 확인합니다.
완전성 검증에는 모든 자산의 자식이 필요하며 노출 계산에는 BTC만 사용합니다.
미래에 도착한 계정이나 자식 행을 과거 자료 선택·완전성 확인에 사용하지 않습니다.

모든 판단 시점과 제외 사유를 남깁니다. 같은 스냅샷을 여러 번 평가한 횟수와 고유 관측 수는 다릅니다.
전체 시장 포괄성, 실패한 요청의 완전성, 실제 과거 신호 게시 시각까지 인증하지는 않습니다.

## 미래 관측 가격의 결과 라벨

`study_events(features, contexts, books, policy, rules)`는 고정한 특징에 미래 결과를 붙입니다.
`observed_outcome`은 상방 우선 `+1`, 하방 우선 `-1`, 미도달 `0`, 또는 null과 사유를 반환합니다.

```text
upper/lower = reference * (1 +/- barrier_bps/10000)
미래 창: decision < received <= decision+horizon
종료 mid 변화(bps) = (terminal_mid/reference-1)*10000
```

공개 예제는 60/300초 horizon, 경계 포함 ±10bps 장벽, 최대 관측 공백/나이 5초를 사용합니다.
규칙은 결과를 계산하기 전에 정합니다.

- 조기 장벽 도달 후에도 전체 horizon의 적격한 관측이 필요합니다.
- 관측 종료, 긴 공백, 오래된 가격, 시간 오류와 session 변경은 결과를 검열합니다.
- 같은 최초 수신 시각에 상·하 장벽 도달이 함께 있으면 순서가 모호합니다.
- 종료 시각 이전의 마지막 신선한 미래 관측을 사용하며 보간이나 이후 가격을 사용하지 않습니다.
  종료 가격이 상충하면 검열합니다.
- LGI/OBI 구간은 `[-1,-0.5)`, `[-0.5,0)`, `[0,0.5)`, `[0.5,1]`로 고정합니다.
- OBI 비교에는 역사 특징을 만든 BBO의 발생/수신 시각 일치와 공통 적격 코호트가 필요합니다.
- 겹치는 시간창은 독립 표본이 아닙니다. 시작점 기준 비중첩 보조 표도 독립성을 보장하지 않습니다.

관측 가격에서 본 장벽 도달이며 연속 가격 경로의 첫 도달, 실제 청산, 체결을 뜻하지 않습니다.
미도달 0은 가격 변화가 0이라는 뜻도 아닙니다. 종료 가격 변화는 전략 손익이 아닙니다.

## 가격 변화와 관측 상태 변화 분해

`examples/research_walkthrough.py`는 두 인공 계정과 미리 고른 +30초→+60초 구간을 사용합니다.
상태를 고정하고 가격만 바꾸거나 가격을 고정하고 상태만 바꿉니다.

```text
B  = F(P_b,S_b)  # 기준
A  = F(P_t,S_t)  # 현재
PO = F(P_t,S_b)  # 가격만 변경
SO = F(P_b,S_t)  # 상태만 변경
price_effect = PO-B
state_effect = SO-B
interaction = A-PO-SO+B
A-B = price_effect + state_effect + interaction
```

동결한 과거 상태의 재가격 계산은 그 상태가 현재도 신선하다는 주장이 아닙니다.
상태에는 실제 수량뿐 아니라 관측 계정 구성·결측·신선도도 영향을 줍니다.
ID/시각만 바뀐 것과 유효성 전환은 구분해야 하며 상태 변화를 모두 매매로 해석하면 안 됩니다.
상호작용 때문에 독립적인 두 효과만 더하면 전체 변화가 되지 않습니다.
순차 배분은 순서·기준에 의존합니다. 이 항등식 검산은 인과 식별이나 예측력 증명이 아닙니다.

## 선택적인 로컬 파일 입출력

`historical_io`와 `event_study_io`는 PyArrow를 사용하는 선택 모듈입니다.
입력 경로·크기·SHA-256·정책·코드 해시를 고정하고 읽기 전후 원문을 확인합니다.
이벤트 연구는 규칙을 등록하고 특징을 재생해 대조한 뒤 결과 라벨과 정적 HTML/SVG를 만듭니다.

출력은 raw 입력 루트와 모든 Git 저장소 밖의 새 경로여야 하며 기존 결과를 덮어쓰지 않습니다.
특정 native schema를 기대하는 어댑터이고 임의의 거래소 파일 로더는 아닙니다.
실제 데이터·정책·manifest·보고서는 배포하지 않습니다.

```bash
python -m market_microstructure.historical_io --help
python -m market_microstructure.event_study_io --help
python examples/event_study_synthetic.py --output /tmp/NEW_SYNTHETIC_REPORT.html
```

마지막 명령은 파일을 생성하는 선택 예제입니다. 기본 데모 명령은 파일을 쓰지 않습니다.
해시와 출력 경로 제한은 재현성·오포함 방지 장치이며 시계 정확성·익명화·비밀 탐지 보장은 아닙니다.

## 해석상의 한계

선택한 계정, 누락된 청산가와 신선도는 표본을 편향시킬 수 있습니다.
청산가 근접도는 거래소 증거금 엔진이 아니며 스냅샷 갱신은 새 거래가 없어도 발생합니다.
시점 정합성만으로 연구자의 선택 편향까지 제거할 수는 없습니다.
직렬 의존, 희소 구간, 바뀌는 코호트와 float의 수치 한계를 고려해야 합니다.
재현 테스트가 확인하는 것은 명시한 계산과 경계 처리이며 시장 일반화·예측 우월성은 별도 검증 대상입니다.
