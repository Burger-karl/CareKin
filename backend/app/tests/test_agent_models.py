"""Tests for the agent toolkit's conversation logs (Day 14, PRD 8.1).

Rows are written straight to the database: the agents that write them arrive in
later steps. These check what the schema itself guarantees: order, defaults,
encryption at rest and the uniqueness rules.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.models import (
    AgentChannel,
    AgentConversation,
    AgentKind,
    AgentMessage,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
)
from app.core import encryption
from app.core.permissions import UserRole
from app.domains.auth.models import User


@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


async def make_conversation(db: AsyncSession) -> AgentConversation:
    phone = "+234803" + "".join(random.choices("0123456789", k=7))
    user = User(phone=phone, full_name="Ngozi Okafor", role=UserRole.GUARDIAN)
    db.add(user)
    await db.commit()  # no ORM relationship orders these inserts, so commit the user first
    conversation = AgentConversation(
        agent=AgentKind.MATCHING, user_id=user.id, channel=AgentChannel.WEB
    )
    db.add(conversation)
    await db.commit()
    return conversation


def message(conversation: AgentConversation, position: int, **fields) -> AgentMessage:
    values = {"role": MessageRole.USER, "content": "Find a carer for my son"}
    values.update(fields)
    return AgentMessage(conversation_id=conversation.id, position=position, **values)


async def test_messages_come_back_in_order_with_defaults(db) -> None:
    conversation = await make_conversation(db)
    # Added out of order, in one transaction (so created_at is identical for all three).
    db.add_all(
        [
            message(conversation, 2, role=MessageRole.ASSISTANT, content="Here are three."),
            message(conversation, 0),
            message(conversation, 1, role=MessageRole.TOOL, content="{}", tool_name="match"),
        ]
    )
    await db.commit()

    loaded = await db.scalar(
        select(AgentConversation)
        .where(AgentConversation.id == conversation.id)
        .options(selectinload(AgentConversation.messages))
        .execution_options(populate_existing=True)
    )
    assert loaded is not None
    assert [m.role for m in loaded.messages] == [
        MessageRole.USER,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert loaded.status is ConversationStatus.ACTIVE
    assert all(m.guardrail_flag is GuardrailFlag.NONE for m in loaded.messages)
    assert loaded.handoff_incident_id is None and loaded.handed_off_at is None


async def test_message_content_is_encrypted_at_rest(db) -> None:
    conversation = await make_conversation(db)
    db.add(message(conversation, 0, content="My son has sickle cell and a fever"))
    await db.commit()

    raw = await db.scalar(text("SELECT content FROM agent_messages"))
    assert isinstance(raw, bytes)
    assert b"sickle" not in raw and b"fever" not in raw
    stored = await db.scalar(select(AgentMessage.content))
    assert stored == "My son has sickle cell and a fever"


async def test_two_messages_cannot_share_a_position(db) -> None:
    conversation = await make_conversation(db)
    db.add_all([message(conversation, 0), message(conversation, 0, content="again")])
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_a_whatsapp_message_is_stored_once(db) -> None:
    conversation = await make_conversation(db)
    db.add_all(
        [
            message(conversation, 0, wa_message_id="wamid.ABC"),
            message(conversation, 1, wa_message_id="wamid.ABC"),
        ]
    )
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_a_conversation_with_messages_cannot_be_deleted(db) -> None:
    conversation = await make_conversation(db)
    db.add(message(conversation, 0))
    await db.commit()
    with pytest.raises(IntegrityError):
        await db.execute(
            text("DELETE FROM agent_conversations WHERE id = :id"), {"id": conversation.id}
        )


async def test_a_handoff_is_recorded_on_the_conversation(db) -> None:
    conversation = await make_conversation(db)
    conversation.status = ConversationStatus.HANDED_OFF
    conversation.handed_off_at = datetime.now(UTC)
    await db.commit()

    status = await db.scalar(
        text("SELECT status::text FROM agent_conversations WHERE id = :id"),
        {"id": conversation.id},
    )
    assert status == "handed_off"
