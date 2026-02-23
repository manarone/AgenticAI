"""Telegram webhook payload and response contracts."""

from pydantic import BaseModel, Field


class TelegramFromUser(BaseModel):
    """Subset of Telegram user data required by the webhook."""

    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


class TelegramMessage(BaseModel):
    """Subset of Telegram message data required by the webhook."""

    text: str | None = Field(default=None, max_length=8192)
    from_user: TelegramFromUser | None = Field(default=None, alias="from")


class TelegramUpdate(BaseModel):
    """Top-level Telegram update."""

    update_id: int
    message: TelegramMessage | None = None
    edited_message: TelegramMessage | None = None


class TelegramWebhookAck(BaseModel):
    """Webhook acknowledgement payload."""

    ok: bool = True
    status: str
    update_id: int
    duplicate: bool = False
    task_id: str | None = None
