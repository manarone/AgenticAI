from datetime import timedelta

from libs.common.db import AsyncSessionLocal
from libs.common.models import InviteCode
from libs.common.repositories import CoreRepository


async def test_invite_code_one_time_use_and_expiry():
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, _, _ = await repo.get_or_create_default_tenant_user()
        invite = await repo.create_invite_code(tenant.id, ttl_hours=24)
        await db.commit()

        ok, _ = await repo.redeem_invite_code(invite.code, telegram_user_id='1001')
        await db.commit()
        assert ok

        ok2, msg2 = await repo.redeem_invite_code(invite.code, telegram_user_id='1002')
        assert ok2 is False
        assert 'already used' in msg2.lower()

        expired = InviteCode(tenant_id=tenant.id, code='expired-code', expires_at=invite.expires_at - timedelta(days=2), used=False)
        db.add(expired)
        await db.commit()
        ok3, msg3 = await repo.redeem_invite_code('expired-code', telegram_user_id='1003')
        assert ok3 is False
        assert 'expired' in msg3.lower()
