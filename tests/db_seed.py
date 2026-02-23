"""Shared helpers for seeding test databases."""

from sqlalchemy.orm import Session

from agenticai.db.base import Base
from agenticai.db.models import Organization, User
from agenticai.db.session import build_engine


def seed_identity_database(
    database_url: str,
    *,
    org_id: str,
    org_slug: str,
    org_name: str,
    user_id: str,
    telegram_user_id: int,
    display_name: str,
) -> None:
    """Initialize schema and insert one organization/user identity."""
    engine = build_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
        session.add(
            Organization(
                id=org_id,
                slug=org_slug,
                name=org_name,
            )
        )
        session.add(
            User(
                id=user_id,
                org_id=org_id,
                telegram_user_id=telegram_user_id,
                display_name=display_name,
            )
        )
        session.commit()
    engine.dispose()
