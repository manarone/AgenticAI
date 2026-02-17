import secrets

from fastapi import Header, HTTPException, status

from libs.common.config import get_settings


async def require_admin_token(x_admin_token: str = Header(default='')) -> None:
    settings = get_settings()
    configured = settings.admin_token.strip()
    if not configured or configured == 'change-me':
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Admin token not configured')
    if not x_admin_token or not secrets.compare_digest(x_admin_token, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid admin token')
