"""Shared S3-02 domain vocabulary; behavior is implemented in later work packages.

These values match the first database migration. They do not authorize state
transitions, refunds, access, or deletion by themselves.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class InputKind(StrEnum):
    LOCAL = "LOCAL"
    URL = "URL"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureCategory(StrEnum):
    USER_INPUT = "USER_INPUT"
    INTERNAL = "INTERNAL"
    EXTERNAL_TIMEOUT = "EXTERNAL_TIMEOUT"
    EXTERNAL_RATE_LIMIT = "EXTERNAL_RATE_LIMIT"
    EXTERNAL_UNAVAILABLE = "EXTERNAL_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    USER_CANCELLED = "USER_CANCELLED"


class QuotaEventType(StrEnum):
    SPEND = "SPEND"
    REFUND = "REFUND"
    RECOVER = "RECOVER"


class AssetCleanupState(StrEnum):
    REGISTERED = "REGISTERED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class CleanupJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    COMPLETED = "COMPLETED"


class BackupCleanupState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AuditRetentionClass(StrEnum):
    TASK_30D = "TASK_30D"
    DELETION_90D = "DELETION_90D"


@dataclass(frozen=True, slots=True)
class TaskRef:
    task_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True)
class QueueKey:
    submitted_at: datetime
    user_id: UUID
    task_id: UUID


@dataclass(frozen=True, slots=True)
class LeaseToken:
    task_id: UUID
    user_id: UUID
    generation: int
    owner: str
    expires_at: datetime
