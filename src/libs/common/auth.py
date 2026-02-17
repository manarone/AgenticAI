from fastapi import Header, HTTPException, status

from libs.common.config import get_settings


async def require_admin_token(x_admin_token: str = Header(default='')) -> None:
    settings = get_settings()
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid admin token')
