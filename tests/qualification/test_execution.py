import pytest

from tools.qualification.execution import ExecutionGuard
from tools.qualification.resources import Budget, ResourceLimit


def test_sleep_hold_released_after_failure():
    calls = []

    def state(flags):
        calls.append(flags)
        return 0x80000000

    with pytest.raises(ValueError, match='work failed'):
        with ExecutionGuard(keep_awake=True, set_state=state):
            raise ValueError('work failed')
    assert calls == [0x80000001, 0x80000000]


def test_hold_failure_does_not_start_work():
    with pytest.raises(RuntimeError, match='establish'):
        with ExecutionGuard(keep_awake=True, set_state=lambda flags: 0):
            pytest.fail('work must not start')


def test_clock_gap_invalidates_budget_and_completion(tmp_path):
    now = [100.0]
    guard = ExecutionGuard(clock=lambda: now[0], interval=60)
    with pytest.raises(ResourceLimit, match='continuity'):
        with guard:
            now[0] += 120
            guard.observe()
            assert guard.evidence()['continuity_lost']
            with pytest.raises(ResourceLimit, match='continuity'):
                Budget(tmp_path, invariant=guard.check).check()


def test_regular_heartbeat_preserves_valid_evidence():
    now = [100.0]
    with ExecutionGuard(clock=lambda: now[0], interval=60) as guard:
        for _ in range(10):
            now[0] += 1
            guard.observe()
            guard.check()
    assert not guard.evidence()['continuity_lost']
    assert guard.evidence()['maximum_scheduling_gap_seconds'] == 1
