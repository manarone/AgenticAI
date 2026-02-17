from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.repositories import CoreRepository


async def append_audit(
    db: AsyncSession,
    tenant_id: str,
    actor: str,
    action: str,
    details: dict,
    user_id: str | None = None,
) -> None:
    repo = CoreRepository(db)
    await repo.log_audit(tenant_id=tenant_id, actor=actor, action=action, details=details, user_id=user_id)
