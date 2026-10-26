"""Tests for the care companion (Day 15): reminders, the companion turn, its history,
and the WhatsApp webhook, end to end.

Real database, real routes (the companion's tools go through /patients/{id}/reminders
as the user), real guardrails, Redis for webhook dedupe. Only the model (MockLLMClient)
and WhatsApp (MockWhatsAppClient) are stand-ins.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select

from app.agents.companion import ReminderArgs
from app.agents.models import (
    AgentChannel,
    AgentConversation,
    AgentKind,
    AgentMessage,
    ConversationStatus,
    GuardrailFlag,
    MessageRole,
    Reminder,
    ReminderKind,
)
from app.agents.reminders import (
    MAX_ACTIVE_REMINDERS,
    WHATSAPP_TEMPLATES,
    next_due,
    send_due_reminders,
)
from app.config import get_settings
from app.core import encryption
from app.core.audit import AuditLog
from app.core.permissions import UserRole
from app.domains.bookings.service import LAGOS
from app.domains.patients.models import Guardian
from app.domains.trust.models import Incident, IncidentStatus
from app.integrations import llm
from app.integrations.llm import LLMUnavailable, text_reply, tool_reply
from app.integrations.sms import MockSMSClient
from app.integrations.whatsapp import (
    MOCK_APP_SECRET,
    MOCK_VERIFY_TOKEN,
    MockWhatsAppClient,
    get_whatsapp_client,
    sign_body,
)
from app.main import app
from app.tests.test_agent_match import consent
from app.tests.test_caregiver_matching import auth, make_guardian, make_patient, make_user

MESSAGE = "/agents/companion/message"
WEBHOOK = "/webhooks/whatsapp"


# ----------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


@pytest.fixture
def model():
    """The app's mock model (LLM_PROVIDER=mock in tests), emptied around each test."""
    mock = llm.get_llm_client()
    assert isinstance(mock, llm.MockLLMClient)
    mock.reset()
    yield mock
    mock.reset()


@pytest.fixture
def whatsapp():
    """A fresh WhatsApp outbox per test, wired into the app."""
    mock = MockWhatsAppClient()
    app.dependency_overrides[get_whatsapp_client] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def patient_user(db, *, consented: bool = True):
    """An adult patient with their own login."""
    user, patient = await make_patient(db)
    if consented:
        await consent(db, user)
    return user, patient


def code(response) -> str:
    return response.json()["error"]["code"]


async def say(api, user, message, **extra):
    return await api.post(MESSAGE, json={"message": message, **extra}, headers=auth(user))


async def log_of(db, session_id) -> list[tuple[MessageRole, str]]:
    rows = await db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == session_id)
        .order_by(AgentMessage.position)
    )
    return [(row.role, row.tool_name or row.content) for row in rows]


async def count(db, model_cls) -> int:
    return await db.scalar(select(func.count()).select_from(model_cls))


def reminders_url(patient) -> str:
    return f"/patients/{patient.id}/reminders"


async def add_reminder(api, user, patient, **body):
    data = {"kind": "hydration", "local_time": "10:00", **body}
    return await api.post(reminders_url(patient), json=data, headers=auth(user))


# ----------------------------------------------------------------- schedule
AFTER = datetime(2026, 10, 26, 9, 0, tzinfo=LAGOS)  # a Monday, 09:00 in Lagos
EVERY_DAY = list(range(7))


def test_a_reminder_later_today_fires_today() -> None:
    due = next_due(time(10, 0), EVERY_DAY, None, AFTER)
    assert due == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # 10:00 Lagos is 09:00 UTC


def test_a_time_already_past_fires_tomorrow() -> None:
    due = next_due(time(8, 0), EVERY_DAY, None, AFTER)
    assert due.astimezone(LAGOS) == datetime(2026, 10, 27, 8, 0, tzinfo=LAGOS)


def test_only_the_chosen_days_count() -> None:
    due = next_due(time(8, 0), [4], None, AFTER)  # Fridays
    assert due.astimezone(LAGOS) == datetime(2026, 10, 30, 8, 0, tzinfo=LAGOS)


def test_the_same_day_next_week_when_today_has_passed() -> None:
    due = next_due(time(8, 0), [0], None, AFTER)  # Mondays, and it's Monday 09:00
    assert due.astimezone(LAGOS) == datetime(2026, 11, 2, 8, 0, tzinfo=LAGOS)


def test_a_one_off_fires_once() -> None:
    assert next_due(time(7, 0), [], date(2026, 11, 3), AFTER) is not None
    assert next_due(time(7, 0), [], date(2026, 10, 25), AFTER) is None


# ----------------------------------------------------------------- what the model may set
def test_reminder_arguments_forgive_a_small_model() -> None:
    args = ReminderArgs.model_validate({"kind": "Water", "time": "8pm", "days": "weekdays"})
    assert args.kind is ReminderKind.HYDRATION and args.local_time == time(20, 0)
    assert args.days == ["mon", "tue", "wed", "thu", "fri"]
    args = ReminderArgs.model_validate({"kind": "check in", "time": "12am", "days": "daily"})
    assert args.kind is ReminderKind.CHECK_IN and args.local_time == time(0, 0)
    assert args.days is None  # every day is the default
    args = ReminderArgs.model_validate(
        {"kind": "clinic appointment", "time": "7:30", "date": "tomorrow"}
    )
    assert args.kind is ReminderKind.APPOINTMENT and args.on_date is not None
    args = ReminderArgs.model_validate(
        {"kind": "medicine", "time": "08:00", "days": ["Monday", "friday"]}
    )
    assert args.days == ["mon", "fri"]


def test_dates_can_be_written_as_people_say_them() -> None:
    def on(text: str) -> date:
        return ReminderArgs.model_validate(
            {"kind": "appointment", "time": "7am", "date": text}
        ).on_date

    today = datetime.now(LAGOS).date()
    assert on("3 Nov 2030") == on("November 3rd, 2030") == date(2030, 11, 3)
    upcoming = on("3 November")
    assert (upcoming.month, upcoming.day) == (11, 3) and today <= upcoming < date(
        today.year + 1, 11, 4
    )
    with pytest.raises(ValueError, match="appointment needs its date"):
        ReminderArgs.model_validate({"kind": "appointment", "time": "7am"})


def test_unreadable_arguments_get_a_message_the_model_can_act_on() -> None:
    with pytest.raises(ValueError, match="time"):
        ReminderArgs.model_validate({"kind": "water", "time": "25pm"})
    with pytest.raises(ValueError, match="kind"):
        ReminderArgs.model_validate({"kind": "dancing", "time": "8am"})


# ----------------------------------------------------------------- reminder routes
async def test_a_patient_sets_a_reminder(api, db) -> None:
    user, patient = await patient_user(db)
    response = await add_reminder(api, user, patient, days=["fri", "mon", "mon"])

    assert response.status_code == 201, response.text
    out = response.json()
    assert out["days"] == ["mon", "fri"] and out["channel"] == "whatsapp" and out["active"]
    assert out["local_time"] == "10:00:00" and out["next_due_at"] is not None
    assert out["text"] == "CareKin reminder for Chidi: time for a drink of water."
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "reminder.create"))
    assert audit is not None and audit.actor_id == user.id


async def test_medication_reminders_never_name_a_medicine(api, db) -> None:
    user, patient = await patient_user(db)
    out = (await add_reminder(api, user, patient, kind="medication")).json()
    assert out["text"] == "CareKin reminder for Chidi: time for the medicine the doctor prescribed."


async def test_a_guardian_sets_reminders_for_a_minor(api, db) -> None:
    _, child = await make_patient(db, minor=True)
    guardian = await make_guardian(db, child)
    response = await add_reminder(api, guardian, child, kind="check_in", local_time="18:30")
    assert response.status_code == 201, response.text
    assert "How is Chidi today?" in response.json()["text"]


async def test_a_one_off_in_the_past_is_refused(api, db) -> None:
    user, patient = await patient_user(db)
    yesterday = (datetime.now(LAGOS) - timedelta(days=1)).date().isoformat()
    response = await add_reminder(api, user, patient, kind="appointment", on_date=yesterday)
    assert response.status_code == 400 and code(response) == "reminder_in_past"


async def test_days_and_a_date_together_are_rejected(api, db) -> None:
    user, patient = await patient_user(db)
    response = await add_reminder(api, user, patient, days=["mon"], on_date="2030-01-01")
    assert response.status_code == 422


async def test_reminders_are_capped(api, db) -> None:
    user, patient = await patient_user(db)
    for _ in range(MAX_ACTIVE_REMINDERS):
        assert (await add_reminder(api, user, patient)).status_code == 201
    response = await add_reminder(api, user, patient)
    assert response.status_code == 409 and code(response) == "too_many_reminders"


async def test_someone_elses_patient_is_not_found(api, db) -> None:
    user, _ = await patient_user(db)
    _, stranger = await make_patient(db)
    response = await add_reminder(api, user, stranger)
    assert response.status_code == 404


async def test_caregivers_cannot_set_reminders(api, db) -> None:
    _, patient = await make_patient(db)
    caregiver = await make_user(db, UserRole.CAREGIVER)
    response = await add_reminder(api, caregiver, patient)
    assert response.status_code == 403 and code(response) == "role_forbidden"


async def test_each_person_sees_only_their_own_reminders(api, db) -> None:
    user, patient = await patient_user(db)
    guardian = await make_guardian(db, patient)
    await add_reminder(api, user, patient)
    await add_reminder(api, guardian, patient, kind="medication")
    mine = (await api.get(reminders_url(patient), headers=auth(user))).json()
    assert [r["kind"] for r in mine] == ["hydration"]


async def test_switching_a_reminder_off(api, db) -> None:
    user, patient = await patient_user(db)
    reminder = (await add_reminder(api, user, patient)).json()
    response = await api.patch(
        f"{reminders_url(patient)}/{reminder['id']}", json={"active": False}, headers=auth(user)
    )
    assert response.status_code == 200, response.text
    assert response.json()["active"] is False and response.json()["next_due_at"] is None


async def test_moving_a_reminder_to_a_one_off(api, db) -> None:
    user, patient = await patient_user(db)
    reminder = (await add_reminder(api, user, patient)).json()
    out = (
        await api.patch(
            f"{reminders_url(patient)}/{reminder['id']}",
            json={"on_date": "2030-01-02", "local_time": "07:15"},
            headers=auth(user),
        )
    ).json()
    assert out["days"] == [] and out["on_date"] == "2030-01-02"
    assert out["next_due_at"].startswith("2030-01-02T06:15")


async def test_another_persons_reminder_cannot_be_changed(api, db) -> None:
    user, patient = await patient_user(db)
    guardian = await make_guardian(db, patient)
    theirs = (await add_reminder(api, guardian, patient)).json()
    response = await api.patch(
        f"{reminders_url(patient)}/{theirs['id']}", json={"active": False}, headers=auth(user)
    )
    assert response.status_code == 404 and code(response) == "reminder_not_found"


# ----------------------------------------------------------------- delivery (Celery beat)
async def due_reminder(api, db, user, patient, **body) -> Reminder:
    created = (await add_reminder(api, user, patient, **body)).json()
    reminder = await db.get(Reminder, created["id"])
    await db.refresh(reminder)
    return reminder


async def test_a_due_reminder_is_sent_once_and_moves_on(api, db, whatsapp) -> None:
    user, patient = await patient_user(db)
    reminder = await due_reminder(api, db, user, patient)
    due = reminder.next_due_at
    now = due + timedelta(minutes=1)

    report = await send_due_reminders(db, whatsapp=whatsapp, sms=None, now=now)

    assert (report.sent, report.failed, report.skipped) == (1, 0, 0)
    [sent] = whatsapp.outbox
    assert sent.to == user.phone
    assert sent.template == WHATSAPP_TEMPLATES[ReminderKind.HYDRATION]
    await db.refresh(reminder)
    assert reminder.last_sent_at is not None and reminder.next_due_at == due + timedelta(days=1)
    again = await send_due_reminders(db, whatsapp=whatsapp, sms=None, now=now)
    assert again.sent == 0 and len(whatsapp.outbox) == 1


async def test_an_sms_reminder_sends_the_fixed_text(api, db) -> None:
    user, patient = await patient_user(db)
    reminder = await due_reminder(api, db, user, patient, channel="sms", kind="medication")
    sms = MockSMSClient()
    report = await send_due_reminders(
        db, whatsapp=None, sms=sms, now=reminder.next_due_at + timedelta(seconds=5)
    )
    assert report.sent == 1
    assert sms.outbox[0].body == (
        "CareKin reminder for Chidi: time for the medicine the doctor prescribed."
    )


async def test_a_one_off_switches_itself_off_after_sending(api, db, whatsapp) -> None:
    user, patient = await patient_user(db)
    reminder = await due_reminder(api, db, user, patient, kind="appointment", on_date="2030-01-02")
    await send_due_reminders(
        db, whatsapp=whatsapp, sms=None, now=reminder.next_due_at + timedelta(minutes=1)
    )
    await db.refresh(reminder)
    assert reminder.active is False and reminder.next_due_at is None
    assert len(whatsapp.outbox) == 1


async def test_a_reminder_too_late_to_help_is_skipped(api, db, whatsapp) -> None:
    user, patient = await patient_user(db)
    reminder = await due_reminder(api, db, user, patient)
    late = reminder.next_due_at + timedelta(minutes=get_settings().REMINDER_GRACE_MINUTES + 5)
    report = await send_due_reminders(db, whatsapp=whatsapp, sms=None, now=late)
    assert (report.sent, report.skipped) == (0, 1) and whatsapp.outbox == []
    await db.refresh(reminder)
    assert reminder.active and reminder.next_due_at > late  # waits for the next one


async def test_a_removed_guardian_stops_getting_reminders(api, db, whatsapp) -> None:
    _, child = await make_patient(db, minor=True)
    guardian = await make_guardian(db, child)
    reminder = await due_reminder(api, db, guardian, child)
    await db.execute(delete(Guardian).where(Guardian.user_id == guardian.id))
    await db.commit()
    report = await send_due_reminders(
        db, whatsapp=whatsapp, sms=None, now=reminder.next_due_at + timedelta(minutes=1)
    )
    assert report.stopped == 1 and whatsapp.outbox == []
    await db.refresh(reminder)
    assert reminder.active is False


async def test_an_unconfigured_channel_counts_as_failed(api, db) -> None:
    user, patient = await patient_user(db)
    reminder = await due_reminder(api, db, user, patient)
    report = await send_due_reminders(
        db, whatsapp=None, sms=None, now=reminder.next_due_at + timedelta(minutes=1)
    )
    assert report.failed == 1


# ----------------------------------------------------------------- the companion (app)
async def test_the_model_sets_a_reminder_and_our_code_confirms_it(api, db, model) -> None:
    user, patient = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "10am"}),
        text_reply("Done! I also booked your appointment."),  # never asked for
    )
    response = await say(api, user, "Remind me to drink water at 10am")

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["status"] == "answered" and out["reply_source"] == "fallback"
    assert out["reply"] == "Done: I'll remind you about water at 10:00 every day."
    assert out["reminder"]["kind"] == "hydration" and out["reminder"]["local_time"] == "10:00:00"
    assert len(model.requests) == 1  # no second, slow call to describe it
    row = await db.scalar(select(Reminder).where(Reminder.patient_id == patient.id))
    assert row is not None and row.created_by == user.id
    assert await log_of(db, out["session_id"]) == [
        (MessageRole.USER, "Remind me to drink water at 10am"),
        (MessageRole.TOOL, "add_reminder"),
        (MessageRole.SYSTEM, out["reply"]),
    ]


async def test_the_patient_comes_from_our_code_not_the_model(api, db, model) -> None:
    user, patient = await patient_user(db)
    _, other = await make_patient(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "10am", "patient_id": str(other.id)})
    )
    await say(api, user, "Remind me to drink water at 10am")
    row = await db.scalar(select(Reminder))
    assert row.patient_id == patient.id


async def test_listing_reminders_is_answered_from_the_data(api, db, model) -> None:
    user, patient = await patient_user(db)
    await add_reminder(api, user, patient, kind="medication", local_time="20:00")
    await add_reminder(api, user, patient, kind="appointment", on_date="2030-01-02")
    model.queue(tool_reply("list_reminders"))
    out = (await say(api, user, "What reminders do I have?")).json()
    assert out["reply"] == (
        "Your reminders: appointment at 10:00 on Wed 02 Jan; medicine at 20:00 every day."
    )
    tool_row = await db.scalar(select(AgentMessage).where(AgentMessage.role == MessageRole.TOOL))
    assert '"kind":"medicine"' in tool_row.content and '"id"' not in tool_row.content


async def test_bad_arguments_go_back_to_the_model_to_fix(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "lunchtime"}),
        tool_reply("add_reminder", {"kind": "water", "time": "1pm"}),
    )
    out = (await say(api, user, "Remind me to drink water at lunchtime, 1pm")).json()
    assert out["reply"] == "Done: I'll remind you about water at 13:00 every day."


async def test_an_appointment_without_a_date_goes_back_to_the_model(api, db, model) -> None:
    """The live model dropped the date and set a daily appointment reminder."""
    user, _ = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "appointment", "time": "7am"}),
        tool_reply("add_reminder", {"kind": "appointment", "time": "7am", "date": "2030-11-03"}),
    )
    out = (await say(api, user, "Remind me about my clinic on 3 Nov 2030 at 7am")).json()
    assert out["reply"] == "Done: I'll remind you about appointment at 07:00 on Sun 03 Nov."
    assert "appointment needs its date" in model.requests[1]["messages"][-1].content


async def test_chat_gets_no_tools(api, db, model) -> None:
    """The live model turned "I'm tired" into a check-in reminder when it had the tools."""
    user, _ = await patient_user(db)
    model.queue(text_reply("Sorry you're tired. Please rest and talk to your doctor."))
    out = (await say(api, user, "I'm feeling a bit tired today")).json()
    assert out["reply_source"] == "model" and out["reminder"] is None
    assert model.requests[0]["tools"] == []
    assert await count(db, Reminder) == 0


async def test_a_reminder_question_without_a_time_is_asked_back(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("What time should I remind you?"))
    out = (await say(api, user, "Remind me about water")).json()
    assert out["reply"] == "What time should I remind you?" and out["reply_source"] == "model"


async def test_the_answer_to_the_models_question_sets_the_reminder(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(
        text_reply("What time should I remind you?"),
        tool_reply("add_reminder", {"kind": "water", "time": "8pm"}),
    )
    first = (await say(api, user, "Remind me about water")).json()
    out = (await say(api, user, "8pm please", session_id=first["session_id"])).json()
    sent = [(m.role, m.content) for m in model.requests[1]["messages"][1:]]
    assert sent == [
        ("user", "Remind me about water"),
        ("assistant", "What time should I remind you?"),
        ("user", "8pm please"),
    ]
    assert [tool.name for tool in model.requests[1]["tools"]] == ["list_reminders", "add_reminder"]
    assert out["reply"] == "Done: I'll remind you about water at 20:00 every day."


async def test_an_earlier_request_is_not_shown_with_a_new_one(api, db, model) -> None:
    """The live model repeated the earlier request instead of acting on the new one."""
    user, _ = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "10am"}),
        tool_reply("add_reminder", {"kind": "appointment", "time": "7am", "date": "2030-11-03"}),
    )
    first = (await say(api, user, "Remind me to drink water at 10am")).json()
    out = (
        await say(
            api,
            user,
            "Also remind me about my clinic on 3 Nov 2030 at 7am",
            session_id=first["session_id"],
        )
    ).json()
    assert [m.role for m in model.requests[1]["messages"]] == ["system", "user"]
    assert out["reply"] == "Done: I'll remind you about appointment at 07:00 on Sun 03 Nov."


async def test_chat_shows_the_model_the_last_lines(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("Hello! How are you today?"), text_reply("Glad to hear it."))
    first = (await say(api, user, "hi")).json()
    await say(api, user, "I'm well, thanks", session_id=first["session_id"])
    sent = [(m.role, m.content) for m in model.requests[1]["messages"][1:]]
    assert sent == [
        ("user", "hi"),
        ("assistant", "Hello! How are you today?"),
        ("user", "I'm well, thanks"),
    ]


async def test_model_down_answers_with_the_menu(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(LLMUnavailable("ollama", "unreachable (ConnectError)"))
    out = (await say(api, user, "Hello")).json()
    assert out["reply_source"] == "fallback" and "Crisis button" in out["reply"]
    assert (MessageRole.SYSTEM, out["reply"]) in await log_of(db, out["session_id"])


async def test_a_reply_about_medicine_is_withheld(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("Take 5mg of folic acid with water."))
    out = (await say(api, user, "Thanks")).json()
    assert out["reply_source"] == "fallback" and "folic" not in out["reply"]
    rows = await db.scalars(select(AgentMessage).where(AgentMessage.role == MessageRole.ASSISTANT))
    assert [row.guardrail_flag for row in rows] == [GuardrailFlag.CLINICAL]  # logged, not sent


async def test_a_route_refusal_gets_a_plain_reply(api, db, model) -> None:
    user, patient = await patient_user(db)
    for _ in range(MAX_ACTIVE_REMINDERS):
        await add_reminder(api, user, patient)
    model.queue(tool_reply("add_reminder", {"kind": "water", "time": "9am"}))
    out = (await say(api, user, "Remind me about water at 9am")).json()
    assert out["reply_source"] == "fallback" and "manage them in the CareKin app" in out["reply"]


async def test_help_gets_the_menu_without_the_model(api, db, model) -> None:
    user, _ = await patient_user(db)
    out = (await say(api, user, "HELP")).json()
    assert out["reply"].startswith("In an emergency, press the Crisis button")
    assert model.requests == []


async def test_a_guardian_of_several_patients_chats_without_reminder_tools(api, db, model) -> None:
    _, first = await make_patient(db, minor=True)
    guardian = await make_guardian(db, first)
    _, second = await make_patient(db, minor=True)
    db.add(Guardian(patient_id=second.id, user_id=guardian.id, relationship="parent"))
    await db.commit()
    await consent(db, guardian)
    model.queue(text_reply("Hello!"))
    out = (await say(api, guardian, "hi")).json()
    assert out["reply"] == "Hello!" and model.requests[0]["tools"] == []
    chosen = (await say(api, guardian, "hi", patient_id=str(second.id))).json()
    conversation = await db.get(AgentConversation, chosen["session_id"])
    assert conversation.patient_id == second.id


# ----------------------------------------------------------------- guardrails (done criterion)
@pytest.mark.parametrize(
    "message",
    [
        "What dose of hydroxyurea should my son take?",
        "Can I give her 2 tablets of paracetamol?",
        "Is this a crisis or just tiredness?",
        "What's wrong with him? Can you diagnose it?",
        "How should we treat the pain at home?",
    ],
)
async def test_dosing_and_diagnosis_questions_are_refused_and_handed_off(
    api, db, model, message
) -> None:
    user, _ = await patient_user(db)
    response = await say(api, user, message)

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["status"] == "handed_off" and out["reply_source"] == "handoff"
    assert out["reply"].startswith("I can't give medical advice")
    assert model.requests == []  # the model never saw it
    conversation = await db.get(AgentConversation, out["session_id"])
    await db.refresh(conversation)
    assert conversation.status is ConversationStatus.HANDED_OFF
    incident = await db.get(Incident, conversation.handoff_incident_id)
    assert incident is not None and incident.status is IncidentStatus.OPEN


@pytest.mark.parametrize("message", ["SOS", "crisis!", "my daughter can't breathe"])
async def test_an_emergency_takes_the_quick_path(api, db, model, message) -> None:
    user, _ = await patient_user(db)
    out = (await say(api, user, message)).json()
    assert out["status"] == "handed_off" and "press the Crisis button" in out["reply"]
    assert model.requests == []


async def test_a_handed_off_conversation_stays_with_a_person(api, db, model) -> None:
    user, _ = await patient_user(db)
    first = (await say(api, user, "SOS")).json()
    again = await say(api, user, "hello?", session_id=first["session_id"])
    assert again.status_code == 409 and code(again) == "conversation_handed_off"


async def test_no_assistant_consent_is_refused(api, db, model) -> None:
    user, _ = await patient_user(db, consented=False)
    response = await say(api, user, "hi")
    assert response.status_code == 403 and code(response) == "consent_required"
    assert await count(db, AgentConversation) == 0


async def test_caregivers_cannot_use_the_companion(api, db, model) -> None:
    caregiver = await make_user(db, UserRole.CAREGIVER)
    response = await say(api, caregiver, "hi")
    assert response.status_code == 403 and code(response) == "role_forbidden"


async def test_a_matching_session_cannot_be_continued_here(api, db, model) -> None:
    user, patient = await patient_user(db)
    matching = AgentConversation(
        agent=AgentKind.MATCHING, user_id=user.id, patient_id=patient.id, channel=AgentChannel.WEB
    )
    db.add(matching)
    await db.commit()
    response = await say(api, user, "hi", session_id=str(matching.id))
    assert response.status_code == 404 and code(response) == "conversation_not_found"


# ----------------------------------------------------------------- history
async def history(api, user, session_id):
    return await api.get(f"/agents/companion/history/{session_id}", headers=auth(user))


async def test_the_owner_sees_what_they_were_shown(api, db, model) -> None:
    user, _ = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "10am"}),
    )
    session = (await say(api, user, "remind me: water at 10am")).json()["session_id"]
    response = await history(api, user, session)

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["view"] == "owner" and out["agent"] == "companion"
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("user", "remind me: water at 10am"),
        ("system", "Done: I'll remind you about water at 10:00 every day."),
    ]
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "agent_conversation.view"))
    assert audit.actor_id == user.id and audit.meta == {"view": "owner", "messages": 2}


async def test_ops_read_the_full_log_of_a_handoff(api, db, model) -> None:
    user, _ = await patient_user(db)
    ops = await make_user(db, UserRole.OPS)
    session = (await say(api, user, "what dose of folic acid?")).json()["session_id"]
    out = (await history(api, ops, session)).json()
    assert out["view"] == "ops" and out["status"] == "handed_off"
    assert out["handoff_incident_id"] is not None
    assert [m["guardrail_flag"] for m in out["messages"]] == ["clinical", "clinical"]


async def test_ops_cannot_read_a_conversation_that_was_not_handed_off(api, db, model) -> None:
    user, _ = await patient_user(db)
    ops = await make_user(db, UserRole.OPS)
    model.queue(text_reply("Hi!"))
    session = (await say(api, user, "hi")).json()["session_id"]
    response = await history(api, ops, session)
    assert response.status_code == 404


async def test_someone_elses_history_is_not_found(api, db, model) -> None:
    user, _ = await patient_user(db)
    other, _ = await patient_user(db)
    model.queue(text_reply("Hi!"))
    session = (await say(api, other, "hi")).json()["session_id"]
    assert (await history(api, user, session)).status_code == 404


# ----------------------------------------------------------------- WhatsApp webhook
def inbound(phone: str, text: str | None = "hi", message_id: str = "wamid.1") -> dict:
    message = {"from": phone.lstrip("+"), "id": message_id, "timestamp": "1761469200"}
    message |= {"type": "text", "text": {"body": text}} if text is not None else {"type": "image"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "1", "changes": [{"field": "messages", "value": {"messages": [message]}}]}
        ],
    }


async def deliver(api, payload: dict, *, secret: str = MOCK_APP_SECRET):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sign_body(secret, body)}
    return await api.post(WEBHOOK, content=body, headers=headers)


def replies(whatsapp: MockWhatsAppClient) -> list[str]:
    return [message.body for message in whatsapp.outbox]


async def test_meta_verifies_the_webhook(api, whatsapp) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": MOCK_VERIFY_TOKEN, "hub.challenge": "42"}
    response = await api.get(WEBHOOK, params=params)
    assert response.status_code == 200 and response.text == "42"
    wrong = await api.get(WEBHOOK, params={**params, "hub.verify_token": "guess"})
    assert wrong.status_code == 403 and code(wrong) == "webhook_verification_failed"


async def test_an_unsigned_webhook_is_refused(api, db, whatsapp) -> None:
    user, _ = await patient_user(db)
    response = await deliver(api, inbound(user.phone), secret="not-the-secret")
    assert response.status_code == 403 and code(response) == "invalid_signature"
    assert whatsapp.outbox == []


async def test_a_whatsapp_message_gets_a_whatsapp_reply(api, db, model, whatsapp) -> None:
    user, patient = await patient_user(db)
    model.queue(
        tool_reply("add_reminder", {"kind": "water", "time": "10am"}),
    )
    response = await deliver(api, inbound(user.phone, "remind me to drink water at 10am"))

    assert response.status_code == 200 and response.json() == {"received": 1, "accepted": 1}
    assert replies(whatsapp) == ["Done: I'll remind you about water at 10:00 every day."]
    assert whatsapp.outbox[0].to == user.phone
    conversation = await db.scalar(select(AgentConversation))
    assert conversation.channel is AgentChannel.WHATSAPP and conversation.patient_id == patient.id
    first = await db.scalar(select(AgentMessage).where(AgentMessage.position == 0))
    assert first.wa_message_id == "wamid.1"
    reminder = await db.scalar(select(Reminder))
    assert reminder.channel.value == "whatsapp"


async def test_a_resent_message_is_answered_once(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("Hello!"))
    await deliver(api, inbound(user.phone))
    again = await deliver(api, inbound(user.phone))
    assert again.json() == {"received": 1, "accepted": 0}
    assert replies(whatsapp) == ["Hello!"]


async def test_the_next_message_continues_the_conversation(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("Hello!"), text_reply("Good."))
    await deliver(api, inbound(user.phone, "hi", "wamid.1"))
    await deliver(api, inbound(user.phone, "I'm fine", "wamid.2"))
    assert await count(db, AgentConversation) == 1
    assert await count(db, AgentMessage) == 4


async def test_an_idle_conversation_is_closed_and_a_new_one_starts(
    api, db, model, whatsapp
) -> None:
    user, _ = await patient_user(db)
    model.queue(text_reply("Hello!"), text_reply("Hello again!"))
    await deliver(api, inbound(user.phone, "hi", "wamid.1"))
    stale = datetime.now(UTC) - timedelta(hours=get_settings().COMPANION_SESSION_IDLE_HOURS + 1)
    old = await db.scalar(select(AgentConversation))
    old.created_at = stale
    await db.execute(
        AgentMessage.__table__.update()
        .where(AgentMessage.conversation_id == old.id)
        .values(created_at=stale)
    )
    await db.commit()
    await deliver(api, inbound(user.phone, "hi", "wamid.2"))
    statuses = (await db.scalars(select(AgentConversation.status))).all()
    assert sorted(statuses) == sorted([ConversationStatus.CLOSED, ConversationStatus.ACTIVE])


async def test_an_emergency_on_whatsapp_gets_the_crisis_link(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db)
    await deliver(api, inbound(user.phone, "my son is in crisis, severe pain"))
    [reply] = replies(whatsapp)
    assert "press the Crisis button" in reply and get_settings().CRISIS_LINK_URL in reply
    assert model.requests == []
    assert await count(db, Incident) == 1


async def test_a_handed_off_whatsapp_conversation_stays_with_a_person(
    api, db, model, whatsapp
) -> None:
    user, _ = await patient_user(db)
    await deliver(api, inbound(user.phone, "SOS", "wamid.1"))
    await deliver(api, inbound(user.phone, "are you there?", "wamid.2"))
    assert replies(whatsapp)[1].startswith("A member of the CareKin team has your conversation")
    assert model.requests == [] and await count(db, Incident) == 1  # no second incident
    conversation = await db.scalar(select(AgentConversation))
    assert (MessageRole.USER, "are you there?") in await log_of(db, conversation.id)


async def test_once_ops_resolve_the_handoff_the_companion_is_back(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db)
    await deliver(api, inbound(user.phone, "SOS", "wamid.1"))
    incident = await db.scalar(select(Incident))
    incident.status = IncidentStatus.RESOLVED
    await db.commit()
    model.queue(text_reply("Hello! How can I help?"))
    await deliver(api, inbound(user.phone, "hi", "wamid.2"))
    assert replies(whatsapp)[1] == "Hello! How can I help?"
    assert await count(db, AgentConversation) == 2


async def test_an_unknown_number_gets_a_fixed_reply_and_nothing_is_stored(
    api, db, model, whatsapp
) -> None:
    await deliver(api, inbound("+2348099999999", "my child is sick"))
    [reply] = replies(whatsapp)
    assert reply.startswith("This is CareKin's care companion") and "112" in reply
    assert await count(db, AgentMessage) == 0 and model.requests == []


async def test_without_consent_only_safety_messages_are_taken(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db, consented=False)
    await deliver(api, inbound(user.phone, "hi", "wamid.1"))
    assert replies(whatsapp)[0].startswith("To chat with CareKin's assistant")
    assert await count(db, AgentMessage) == 0
    await deliver(api, inbound(user.phone, "she is not breathing", "wamid.2"))
    assert "This sounds urgent" in replies(whatsapp)[1]
    assert await count(db, Incident) == 1 and model.requests == []


async def test_stop_switches_off_whatsapp_reminders(api, db, model, whatsapp) -> None:
    user, patient = await patient_user(db)
    await add_reminder(api, user, patient)
    await add_reminder(api, user, patient, channel="sms")
    await deliver(api, inbound(user.phone, "STOP"))
    assert replies(whatsapp) == [
        "Done: 1 WhatsApp reminder switched off. Switch them back on in the CareKin app."
    ]
    active = (await db.scalars(select(Reminder.channel).where(Reminder.active.is_(True)))).all()
    assert [channel.value for channel in active] == ["sms"]


async def test_a_photo_gets_a_text_only_reply(api, db, model, whatsapp) -> None:
    user, _ = await patient_user(db)
    await deliver(api, inbound(user.phone, None))
    assert replies(whatsapp)[0].startswith("Sorry, I can only read text messages")


async def test_other_roles_are_pointed_to_the_app(api, db, model, whatsapp) -> None:
    caregiver = await make_user(db, UserRole.CAREGIVER)
    await deliver(api, inbound(caregiver.phone))
    assert replies(whatsapp)[0].startswith("The care companion is for patients")


async def test_a_failure_still_gives_the_emergency_number(
    api, db, model, whatsapp, monkeypatch
) -> None:
    user, _ = await patient_user(db)

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.agents.companion.take_turn", broken)
    await deliver(api, inbound(user.phone))
    [reply] = replies(whatsapp)
    assert reply.startswith("Sorry, something went wrong") and "112" in reply
