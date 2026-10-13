"""Audit log: who did what, to which record, when and from where (OP-5, HP-4).

Append-only. The app only ever inserts; revoke UPDATE and DELETE on audit_logs
from the application's database role in staging and production (ERD section 6).

Usage inside a service, in the same transaction as the change it records:

    from app.core.audit import write_audit

    write_audit(db, actor_id=user.id, action="consent.grant",
                entity_type="consent_record", entity_id=record.id, request=request)
    await db.commit()

Action names are "<entity>.<verb>", e.g. auth.login, passport.view, escrow.release.
Never put health data, OTP codes or tokens in `metadata`.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.domains.auth.models import User


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # Null for system jobs (Celery tasks, webhooks).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None]  # polymorphic, so no foreign key
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # `metadata` is reserved by SQLAlchemy, so the attribute is `meta`.
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    # Never loaded; it exists so the unit of work inserts a new user before an audit
    # row that references them in the same commit (e.g. registration).
    actor: Mapped[User | None] = relationship("User", lazy="raise", foreign_keys=[actor_id])


def client_ip(request: Request | None) -> str | None:
    """The caller's IP. Behind a proxy, run uvicorn with --proxy-headers so this is real."""
    if request is None or request.client is None:
        return None
    host = request.client.host
    # The test client reports "testclient", which is not a valid inet value.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


def write_audit(
    db: AsyncSession,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    request: Request | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit row to the session. The caller's commit writes it."""
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent", "")[:512] or None) if request else None,
        meta=metadata or None,
    )
    db.add(entry)
    return entry
