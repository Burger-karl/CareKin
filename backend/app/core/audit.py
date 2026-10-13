"""Audit log: who, what, when, from where.

Append-only (ERD section 6): the app only ever inserts. Revoke UPDATE and DELETE
on `audit_logs` for the application role.

Entries are added to the caller's session, so they commit or roll back together
with the change they describe. Never put health data in `metadata`; record
which fields changed, not their values.

Usage in a service:
    audit.record(db, actor, "care_profile.update", "care_profile", profile.id)
    await db.commit()
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, CreatedAtMixin


@dataclass(frozen=True)
class AuditContext:
    """Who is acting and from where. Built once per request."""

    user_id: uuid.UUID | None  # None for system jobs
    ip_address: str | None = None
    user_agent: str | None = None

    @classmethod
    def from_request(
        cls, user_id: uuid.UUID | None, client_host: str | None, user_agent: str | None
    ) -> AuditContext:
        return cls(
            user_id=user_id,
            ip_address=_valid_ip(client_host),
            user_agent=user_agent[:500] if user_agent else None,
        )


def _valid_ip(value: str | None) -> str | None:
    """`inet` rejects anything that isn't an IP, e.g. the test client's 'testclient'."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class AuditLog(CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index(None, "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, sort_order=-100)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100))  # e.g. passport.view
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None]  # polymorphic, no FK
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    # `metadata` is reserved on declarative classes, so the attribute is metadata_.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime]


def record(
    db: AsyncSession,
    ctx: AuditContext,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the session. The caller commits."""
    entry = AuditLog(
        actor_id=ctx.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        metadata_=metadata or {},
    )
    db.add(entry)
    return entry
