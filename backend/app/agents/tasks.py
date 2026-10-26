"""agents Celery tasks. Same pattern as crisis.tasks: a fresh engine per run, because
Celery runs each task in a new event loop."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from app.agents import reminders
from app.core.exceptions import ServiceUnavailableError
from app.domains.crisis.tasks import task_sessions
from app.integrations.sms import SMSClient, get_sms_client
from app.integrations.whatsapp import WhatsAppClient, get_whatsapp_client
from app.worker import celery_app


def _whatsapp() -> WhatsAppClient | None:
    try:
        return get_whatsapp_client()
    except ServiceUnavailableError:
        return None


def _sms() -> SMSClient | None:
    try:
        return get_sms_client()
    except ServiceUnavailableError:
        return None


@celery_app.task(name="agents.send_reminders")
def send_reminders() -> dict[str, int]:
    """Send every reminder that is due. Scheduled by Beat every minute."""

    async def run() -> reminders.ReminderReport:
        async with task_sessions() as sessions, sessions() as db:
            return await reminders.send_due_reminders(db, whatsapp=_whatsapp(), sms=_sms())

    return asdict(asyncio.run(run()))
