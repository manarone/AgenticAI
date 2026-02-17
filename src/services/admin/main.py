from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from libs.common.auth import require_admin_token
from libs.common.db import AsyncSessionLocal, get_db
from libs.common.metrics import REQUEST_COUNTER, metrics_response
from libs.common.models import Base
from libs.common.repositories import CoreRepository


class InviteCodeRequest(BaseModel):
    ttl_hours: int = Field(default=24, ge=1, le=168)
    tenant_name: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as db:
        async with db.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        repo = CoreRepository(db)
        await repo.get_or_create_default_tenant_user()
        await db.commit()
    yield


app = FastAPI(title='agentai-admin', lifespan=lifespan)


@app.get('/healthz')
async def healthz() -> dict:
    REQUEST_COUNTER.labels(service='admin', endpoint='healthz').inc()
    return {'status': 'ok', 'service': 'admin'}


@app.get('/metrics')
async def metrics():
    return metrics_response()


@app.get('/')
async def home(
    _: None = Depends(require_admin_token),
    db=Depends(get_db),
) -> HTMLResponse:
    repo = CoreRepository(db)
    users = await repo.list_users()
    tasks = await repo.list_tasks(limit=10)
    usage = await repo.get_token_usage_summary()

    html = [
        '<html><body><h1>AgentAI Admin MVP</h1>',
        f'<p>Users: {len(users)}</p>',
        f'<p>Recent tasks: {len(tasks)}</p>',
        f'<p>Token usage rows: {len(usage)}</p>',
        '</body></html>',
    ]
    return HTMLResponse(''.join(html))


@app.post('/admin/invite-codes', dependencies=[Depends(require_admin_token)])
async def create_invite_code(payload: InviteCodeRequest, db=Depends(get_db)) -> dict:
    repo = CoreRepository(db)
    tenant, _, _ = await repo.get_or_create_default_tenant_user()

    # MVP scope: tenant_name ignored unless matching existing tenant support is added.
    invite = await repo.create_invite_code(tenant_id=tenant.id, ttl_hours=payload.ttl_hours)
    await db.commit()
    return {'invite_code': invite.code, 'expires_at': invite.expires_at.isoformat(), 'tenant_id': invite.tenant_id}


@app.get('/admin/users', dependencies=[Depends(require_admin_token)])
async def list_users(db=Depends(get_db)) -> list[dict]:
    repo = CoreRepository(db)
    return await repo.list_users()


@app.get('/admin/tasks', dependencies=[Depends(require_admin_token)])
async def list_tasks(db=Depends(get_db)) -> list[dict]:
    repo = CoreRepository(db)
    tasks = await repo.list_tasks(limit=200)
    return [
        {
            'id': t.id,
            'tenant_id': t.tenant_id,
            'user_id': t.user_id,
            'status': t.status.value,
            'task_type': t.task_type,
            'risk_tier': t.risk_tier,
            'attempts': t.attempts,
            'created_at': t.created_at.isoformat(),
            'updated_at': t.updated_at.isoformat(),
        }
        for t in tasks
    ]


@app.get('/admin/token-usage', dependencies=[Depends(require_admin_token)])
async def token_usage(db=Depends(get_db)) -> list[dict]:
    repo = CoreRepository(db)
    return await repo.get_token_usage_summary()
