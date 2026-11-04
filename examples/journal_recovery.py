"""Temporary SQLite demo: committed order recovery, redelivery and conflict."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from market_microstructure.execution import replay_execution
from market_microstructure.execution_journal import ExecutionJournal

if __package__:
    from .execution_walkthrough import synthetic_inputs
else:
    from execution_walkthrough import synthetic_inputs


def main():
    print("SYNTHETIC_TEST_ONLY — 합성 주문 원장 복구; 임시 SQLite 파일은 종료 시 삭제합니다.")
    bundle = synthetic_inputs()
    direct = replay_execution(bundle)
    with TemporaryDirectory(prefix="synthetic-journal-") as directory:
        path = Path(directory) / "orders.sqlite"
        with ExecutionJournal.create(path, bundle) as store:
            events = store.events()
            first = store.record(events[0])
            print(f"진입 저장: {first['status']}, 현금={first['snapshot']['account']['cash']}")
        with ExecutionJournal.open(path) as store:
            recovered = store.recover()
            print(f"재열기·재생: 반영 이벤트={recovered['applied_events']}, "
                  f"현금={recovered['account']['cash']}")
            duplicate = store.record(events[0])
            print(f"같은 진입 재전달: {duplicate['status']}, "
                  f"현금={duplicate['snapshot']['account']['cash']}")
            store.record(events[1])
            conflict = deepcopy(events[0])
            conflict["fill"]["fee"] = "0"
            try:
                store.record(conflict)
            except ValueError:
                print("같은 ID·다른 내용: 거부 (원장 유지)")
            final = store.recover()
            print(f"복구 후 청산: 현금={final['account']['cash']}, 보유={final['account']['positions']}")
            print(f"연속 실행: 현금={direct['terminal_cash']}, 보유={direct['terminal_positions']}")
            assert final["account"]["cash"] == direct["terminal_cash"]
            assert final["account"]["positions"] == direct["terminal_positions"]
            print("연속 실행과 복구 후 실행의 현금·포지션 일치")


if __name__ == "__main__":
    main()
