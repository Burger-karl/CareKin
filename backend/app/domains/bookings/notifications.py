"""SMS notices for booking events (R6, R7 in BOOKINGS_LOG.md).

Best effort: notices are sent after the change is committed, and a failed send is
logged, never raised, so a booking never fails because SMS is down.

Texts carry no names, addresses or health data (SMS isn't private). They point the
reader to the app for details.

TODO: move to the shared notifications module (PRD 10.2 module 4: WhatsApp, SMS,
push, delivery tracking) and a Celery queue once they exist. Today the send happens
inside the request.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.core.phone import mask_phone
from app.integrations.sms import SMSClient

logger = logging.getLogger(__name__)


class Notifier:
    """Sends booking notices. With no SMS client it does nothing."""

    def __init__(self, sms: SMSClient | None) -> None:
        self.sms = sms

    async def send(self, phones: Iterable[str | None], body: str) -> int:
        """Send `body` to each distinct phone. Returns how many were sent."""
        if self.sms is None:
            return 0
        sent = 0
        for phone in dict.fromkeys(p for p in phones if p):
            try:
                await self.sms.send(phone, body)
                sent += 1
            except Exception:
                logger.warning("Booking notice to %s failed", mask_phone(phone), exc_info=True)
        return sent


NO_NOTICES = Notifier(None)


# ---------------------------------------------------------------- texts
def new_request(booking_type: str, when: str) -> str:
    return (
        f"CareKin: You have a new {booking_type.replace('_', '-')} booking request "
        f"for {when}. Open the app to accept or decline."
    )


def approval_needed(when: str) -> str:
    return (
        f"CareKin: A caregiver booking for {when} needs a guardian's approval. "
        "Open the app to review it."
    )


def guardian_rejected(when: str) -> str:
    return f"CareKin: A guardian did not approve the caregiver booking for {when}."


def accepted(when: str) -> str:
    return (
        f"CareKin: Your caregiver accepted the booking for {when}. "
        "Complete payment in the app to confirm it."
    )


def declined(when: str) -> str:
    return (
        f"CareKin: The caregiver declined the booking for {when}. "
        "You can choose another caregiver in the app."
    )


def cancelled(when: str) -> str:
    return f"CareKin: The booking for {when} was cancelled."


def expired(when: str, reason: str) -> str:
    why = "it wasn't paid in time" if reason == "not_paid" else "it wasn't confirmed in time"
    return f"CareKin: The booking for {when} was cancelled because {why}."


def shift_missed(when: str) -> str:
    return (
        f"CareKin: The caregiver did not check in for the shift on {when}. "
        "Contact CareKin support if you need help."
    )


def no_show(when: str) -> str:
    return (
        f"CareKin: The caregiver did not attend the booking for {when}, so it was "
        "cancelled. Any payment will be refunded."
    )


def outside_geofence(kind: str, distance_m: int) -> str:
    action = "in" if kind == "check_in" else "out"
    km = distance_m / 1000
    return (
        f"CareKin: Your caregiver checked {action} about {km:.1f} km from the booking "
        "address. Contact CareKin support if this is unexpected."
    )
