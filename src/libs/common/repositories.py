from __future__ import annotations

from datetime import datetime
from secrets import token_urlsafe

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.enums import ApprovalDecision, TaskStatus
from libs.common.models import (
    Approval,
    AuditLog,
    Conversation,
    InviteCode,
    Message,
    Task,
    TaskEvent,
    TelegramIdentity,
    Tenant,
    TokenUsageDaily,
    User,
)


class CoreRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_default_tenant_user(self) -> tuple[Tenant, User, Conversation]:
        tenant = (await self.db.execute(select(Tenant).limit(1))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name='default')
            self.db.add(tenant)
            await self.db.flush()

        user = (await self.db.execute(select(User).where(User.tenant_id == tenant.id).limit(1))).scalar_one_or_none()
        if user is None:
            user = User(tenant_id=tenant.id, display_name='default-user')
            self.db.add(user)
            await self.db.flush()

        convo = (
            await self.db.execute(
                select(Conversation).where(
                    and_(Conversation.tenant_id == tenant.id, Conversation.user_id == user.id)
                ).limit(1)
            )
        ).scalar_one_or_none()
        if convo is None:
            convo = Conversation(tenant_id=tenant.id, user_id=user.id)
            self.db.add(convo)
            await self.db.flush()

        return tenant, user, convo

    async def create_invite_code(self, tenant_id: str, ttl_hours: int = 24) -> InviteCode:
        from datetime import timedelta

        invite = InviteCode(
            tenant_id=tenant_id,
            code=token_urlsafe(12),
            expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
            used=False,
        )
        self.db.add(invite)
        await self.db.flush()
        return invite

    async def redeem_invite_code(self, code: str, telegram_user_id: str) -> tuple[bool, str | None]:
        invite = (
            await self.db.execute(select(InviteCode).where(InviteCode.code == code).limit(1))
        ).scalar_one_or_none()
        if not invite:
            return False, 'Invite code not found.'
        if invite.used:
            return False, 'Invite code already used.'
        if invite.expires_at < datetime.utcnow():
            return False, 'Invite code expired.'

        user = (
            await self.db.execute(select(User).where(User.tenant_id == invite.tenant_id).limit(1))
        ).scalar_one_or_none()
        if not user:
            user = User(tenant_id=invite.tenant_id, display_name=f'tg-{telegram_user_id}')
            self.db.add(user)
            await self.db.flush()

        existing = (
            await self.db.execute(
                select(TelegramIdentity).where(TelegramIdentity.telegram_user_id == telegram_user_id).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return False, 'Telegram account already linked.'

        identity = TelegramIdentity(
            tenant_id=invite.tenant_id,
            user_id=user.id,
            telegram_user_id=telegram_user_id,
        )
        self.db.add(identity)
        invite.used = True
        await self.db.flush()
        return True, user.id

    async def get_identity(self, telegram_user_id: str) -> TelegramIdentity | None:
        return (
            await self.db.execute(
                select(TelegramIdentity).where(TelegramIdentity.telegram_user_id == telegram_user_id).limit(1)
            )
        ).scalar_one_or_none()

    async def get_identity_by_user_id(self, user_id: str) -> TelegramIdentity | None:
        return (
            await self.db.execute(select(TelegramIdentity).where(TelegramIdentity.user_id == user_id).limit(1))
        ).scalar_one_or_none()

    async def get_or_create_conversation(self, tenant_id: str, user_id: str) -> Conversation:
        convo = (
            await self.db.execute(
                select(Conversation).where(
                    and_(Conversation.tenant_id == tenant_id, Conversation.user_id == user_id)
                ).limit(1)
            )
        ).scalar_one_or_none()
        if convo:
            return convo

        convo = Conversation(tenant_id=tenant_id, user_id=user_id)
        self.db.add(convo)
        await self.db.flush()
        return convo

    async def add_message(self, tenant_id: str, user_id: str, conversation_id: str, role: str, content: str) -> Message:
        message = Message(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def create_task(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        task_type: str,
        risk_tier: str,
        payload: dict,
        status: TaskStatus = TaskStatus.QUEUED,
    ) -> Task:
        task = Task(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            task_type=task_type,
            risk_tier=risk_tier,
            payload=payload,
            status=status,
        )
        self.db.add(task)
        await self.db.flush()
        await self.add_task_event(task.id, tenant_id, status.value, {'payload': payload})
        return task

    async def add_task_event(self, task_id: str, tenant_id: str, status: str, details: dict) -> TaskEvent:
        evt = TaskEvent(task_id=task_id, tenant_id=tenant_id, status=status, details=details)
        self.db.add(evt)
        await self.db.flush()
        return evt

    async def update_task_status(
        self, task_id: str, status: TaskStatus, result: str | None = None, error: str | None = None
    ) -> Task | None:
        task = (await self.db.execute(select(Task).where(Task.id == task_id).limit(1))).scalar_one_or_none()
        if not task:
            return None
        task.status = status
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        await self.add_task_event(task.id, task.tenant_id, status.value, {'result': result, 'error': error})
        await self.db.flush()
        return task

    async def list_user_tasks(self, tenant_id: str, user_id: str) -> list[Task]:
        result = await self.db.execute(
            select(Task)
            .where(and_(Task.tenant_id == tenant_id, Task.user_id == user_id))
            .order_by(Task.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())

    async def list_tasks(self, limit: int = 100) -> list[Task]:
        result = await self.db.execute(select(Task).order_by(Task.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def get_task(self, task_id: str) -> Task | None:
        return (await self.db.execute(select(Task).where(Task.id == task_id).limit(1))).scalar_one_or_none()

    async def increment_task_attempt(self, task_id: str) -> int:
        task = await self.get_task(task_id)
        if not task:
            return 0
        task.attempts += 1
        await self.db.flush()
        return task.attempts

    async def cancel_task(self, task_id: str) -> Task | None:
        task = await self.get_task(task_id)
        if not task:
            return None
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.TIMED_OUT}:
            return task
        return await self.update_task_status(task.id, TaskStatus.CANCELED)

    async def cancel_user_tasks(self, tenant_id: str, user_id: str) -> list[str]:
        tasks = await self.list_user_tasks(tenant_id=tenant_id, user_id=user_id)
        canceled: list[str] = []
        for task in tasks:
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.TIMED_OUT}:
                continue
            task.status = TaskStatus.CANCELED
            canceled.append(task.id)
            await self.add_task_event(task.id, task.tenant_id, TaskStatus.CANCELED.value, {'reason': 'user_cancel'})
        await self.db.flush()
        return canceled

    async def create_approval(self, task_id: str, tenant_id: str, user_id: str) -> Approval:
        approval = Approval(task_id=task_id, tenant_id=tenant_id, user_id=user_id, decision=ApprovalDecision.PENDING)
        self.db.add(approval)
        await self.db.flush()
        return approval

    async def set_approval_decision(self, approval_id: str, decision: ApprovalDecision) -> Approval | None:
        approval = (
            await self.db.execute(select(Approval).where(Approval.id == approval_id).limit(1))
        ).scalar_one_or_none()
        if not approval:
            return None
        approval.decision = decision
        await self.db.flush()
        return approval

    async def get_approval(self, approval_id: str) -> Approval | None:
        return (await self.db.execute(select(Approval).where(Approval.id == approval_id).limit(1))).scalar_one_or_none()

    async def log_audit(self, tenant_id: str, actor: str, action: str, details: dict, user_id: str | None = None) -> AuditLog:
        log = AuditLog(tenant_id=tenant_id, user_id=user_id, actor=actor, action=action, details=details)
        self.db.add(log)
        await self.db.flush()
        return log

    async def increment_token_usage(self, tenant_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
        usage_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        existing = (
            await self.db.execute(
                select(TokenUsageDaily).where(
                    and_(
                        TokenUsageDaily.tenant_id == tenant_id,
                        TokenUsageDaily.model == model,
                        func.date(TokenUsageDaily.usage_date) == usage_date.date(),
                    )
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.input_tokens += input_tokens
            existing.output_tokens += output_tokens
        else:
            self.db.add(
                TokenUsageDaily(
                    tenant_id=tenant_id,
                    model=model,
                    usage_date=usage_date,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
        await self.db.flush()

    async def get_token_usage_summary(self) -> list[dict]:
        result = await self.db.execute(
            select(
                TokenUsageDaily.tenant_id,
                TokenUsageDaily.model,
                func.sum(TokenUsageDaily.input_tokens),
                func.sum(TokenUsageDaily.output_tokens),
            ).group_by(TokenUsageDaily.tenant_id, TokenUsageDaily.model)
        )
        rows = []
        for tenant_id, model, input_tokens, output_tokens in result.all():
            rows.append(
                {
                    'tenant_id': tenant_id,
                    'model': model,
                    'input_tokens': int(input_tokens or 0),
                    'output_tokens': int(output_tokens or 0),
                }
            )
        return rows

    async def list_users(self) -> list[dict]:
        result = await self.db.execute(
            select(User.id, User.display_name, User.tenant_id, TelegramIdentity.telegram_user_id)
            .join(TelegramIdentity, TelegramIdentity.user_id == User.id, isouter=True)
            .order_by(User.created_at.desc())
        )
        users: list[dict] = []
        for user_id, display_name, tenant_id, telegram_user_id in result.all():
            users.append(
                {
                    'id': user_id,
                    'display_name': display_name,
                    'tenant_id': tenant_id,
                    'telegram_user_id': telegram_user_id,
                }
            )
        return users
