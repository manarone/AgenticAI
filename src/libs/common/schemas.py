from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from libs.common.enums import RiskTier, TaskType


class PermissionFiles(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class PermissionNetwork(BaseModel):
    allow_domains: list[str] = Field(default_factory=list)


class PermissionEnv(BaseModel):
    allow: list[str] = Field(default_factory=list)


class SkillPermissions(BaseModel):
    files: PermissionFiles = Field(default_factory=PermissionFiles)
    network: PermissionNetwork = Field(default_factory=PermissionNetwork)
    env: PermissionEnv = Field(default_factory=PermissionEnv)


class SkillManifest(BaseModel):
    name: str
    version: str
    risk_tier: RiskTier
    permissions: SkillPermissions = Field(default_factory=SkillPermissions)
    requires_approval_actions: list[str] = Field(default_factory=list)


class TaskEnvelope(BaseModel):
    task_id: UUID
    tenant_id: UUID
    user_id: UUID
    task_type: TaskType
    payload: dict[str, Any]
    risk_tier: RiskTier
    approval_id: UUID | None = None
    created_at: datetime


class TaskResult(BaseModel):
    task_id: UUID
    tenant_id: UUID
    user_id: UUID
    success: bool
    output: str
    error: str | None = None
    created_at: datetime
