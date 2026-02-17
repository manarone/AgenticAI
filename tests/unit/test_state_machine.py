from libs.common.enums import TaskStatus
from libs.common.state_machine import can_transition


def test_task_state_machine_enforced():
    assert can_transition(TaskStatus.QUEUED, TaskStatus.DISPATCHING)
    assert not can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
