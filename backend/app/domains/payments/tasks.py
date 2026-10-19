"""payments Celery tasks. Same pattern as crisis.tasks: a fresh engine per run, because
Celery runs each task in a new event loop."""

from __future__ import annotations

import asyncio

from app.core.exceptions import ServiceUnavailableError
from app.domains.bookings.notifications import Notifier
from app.domains.crisis.tasks import task_sessions
from app.domains.payments import service
from app.integrations.escrow import get_escrow_client
from app.integrations.sms import get_sms_client
from app.worker import celery_app


def _notifier() -> Notifier:
    try:
        return Notifier(get_sms_client())
    except ServiceUnavailableError:
        return Notifier(None)


@celery_app.task(name="payments.settle_escrows")
def settle_escrows() -> dict[str, int]:
    """Reconcile payments, then release and refund escrow by rule. Every 10 minutes."""

    async def run() -> dict[str, int]:
        async with task_sessions() as sessions, sessions() as db:
            result = await service.settle_escrows(
                db,
                gateways=service.get_gateways(),
                escrow_client=get_escrow_client(),
                notifier=_notifier(),
            )
            return result.as_dict()

    return asyncio.run(run())
