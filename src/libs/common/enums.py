from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = 'QUEUED'
    DISPATCHING = 'DISPATCHING'
    RUNNING = 'RUNNING'
    WAITING_APPROVAL = 'WAITING_APPROVAL'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELED = 'CANCELED'
    TIMED_OUT = 'TIMED_OUT'


class ApprovalDecision(str, Enum):
    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    DENIED = 'DENIED'
    EXPIRED = 'EXPIRED'


class RiskTier(str, Enum):
    L1 = 'L1'
    L2 = 'L2'
    L3 = 'L3'


class TaskType(str, Enum):
    SKILL = 'skill'
    SHELL = 'shell'
    FILE = 'file'
    WEB = 'web'
