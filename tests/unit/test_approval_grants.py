from datetime import datetime, timedelta

from sqlalchemy import select

from libs.common.db import AsyncSessionLocal
from libs.common.enums import ApprovalDecision
from libs.common.models import ApprovalGrant, User
from libs.common.repositories import CoreRepository


async def test_issue_and_revoke_approval_grant():
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, _ = await repo.get_or_create_default_tenant_user()

        grant, refreshed = await repo.issue_approval_grant(
            tenant_id=tenant.id,
            user_id=user.id,
            scope='shell_mutation',
            ttl_minutes=10,
        )
        assert refreshed is False
        assert grant.expires_at > datetime.utcnow()
        assert await repo.has_active_approval_grant(tenant.id, user.id, 'shell_mutation') is True

        revoked = await repo.revoke_approval_grants(tenant.id, user.id, scope='shell_mutation')
        assert revoked == 1
        assert await repo.has_active_approval_grant(tenant.id, user.id, 'shell_mutation') is False


async def test_approval_grant_is_tenant_user_scoped_and_expires():
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, _ = await repo.get_or_create_default_tenant_user()
        other_user = User(tenant_id=tenant.id, display_name='another-user')
        db.add(other_user)
        await db.flush()

        grant, _ = await repo.issue_approval_grant(
            tenant_id=tenant.id,
            user_id=user.id,
            scope='shell_mutation',
            ttl_minutes=10,
        )
        assert await repo.has_active_approval_grant(tenant.id, user.id, 'shell_mutation') is True
        assert await repo.has_active_approval_grant(tenant.id, other_user.id, 'shell_mutation') is False

        stored = (await db.execute(select(ApprovalGrant).where(ApprovalGrant.id == grant.id))).scalar_one()
        stored.expires_at = datetime.utcnow() - timedelta(minutes=1)
        await db.flush()
        assert await repo.has_active_approval_grant(tenant.id, user.id, 'shell_mutation') is False

        renewed, refreshed = await repo.issue_approval_grant(
            tenant_id=tenant.id,
            user_id=user.id,
            scope='shell_mutation',
            ttl_minutes=10,
        )
        assert refreshed is False
        assert renewed.id != grant.id


async def test_set_approval_decision_is_single_use():
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='SHELL',
            risk_tier='HIGH',
            payload={'command': 'systemctl restart nginx'},
        )
        approval = await repo.create_approval(task.id, tenant.id, user.id)

        first = await repo.set_approval_decision(approval.id, ApprovalDecision.APPROVED)
        second = await repo.set_approval_decision(approval.id, ApprovalDecision.APPROVED)

        assert first is not None
        assert first.decision == ApprovalDecision.APPROVED
        assert second is None
