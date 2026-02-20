from libs.common.enums import TaskStatus


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.DISPATCHING, TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.DISPATCHING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.QUEUED,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.TIMED_OUT,
        TaskStatus.WAITING_APPROVAL,
    },
    TaskStatus.WAITING_APPROVAL: {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELED: set(),
    TaskStatus.TIMED_OUT: set(),
}


def can_transition(current: TaskStatus, nxt: TaskStatus) -> bool:
    return nxt in ALLOWED_TRANSITIONS[current]
