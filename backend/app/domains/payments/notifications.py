"""SMS texts for payment and escrow events. Sent with the bookings Notifier, after commit,
best effort. No names, amounts or addresses: SMS isn't private."""

from __future__ import annotations


def paid_to_caregiver(when: str) -> str:
    return (
        f"CareKin: The booking for {when} is paid. The money is held safely in escrow "
        "and released to you after the care is completed."
    )


def paid_to_family(when: str) -> str:
    return (
        f"CareKin: Payment for the booking on {when} was received and is held in escrow. "
        "It is only released to the caregiver after the care is completed."
    )


def released(when: str) -> str:
    return f"CareKin: Payment for the booking on {when} has been released to you."


def refunded(when: str) -> str:
    return (
        f"CareKin: Payment for the booking on {when} is being refunded to the payer. "
        "Refunds can take a few working days to arrive."
    )


def dispute_opened(when: str) -> str:
    return (
        f"CareKin: A dispute was opened on the booking for {when}. Payment stays on hold "
        "while CareKin reviews it. We will contact you."
    )


def dispute_closed(when: str) -> str:
    return f"CareKin: The dispute on the booking for {when} has been closed. See the app."
