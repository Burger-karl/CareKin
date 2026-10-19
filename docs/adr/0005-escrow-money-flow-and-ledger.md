# ADR 0005: Escrowed payments with a double-entry ledger, guarded by the database

- **Status:** Proposed
- **Date:** 2026-10-18
- **Owner:** Backend
- **PRD refs:** BK-2, BK-5, BK-6, TS-2, TS-5; section 6 ground rules; build plan Day 11

## Context

Families pay for bookings, sometimes from abroad. The money has to be held until the care
happens, then paid to the caregiver or returned to the payer. Several ground rules apply:

- CareKin never holds funds.
- Every money movement is logged.
- A human or a rule moves money, never an agent.
- Money stays held while a dispute is open.

Things that will go wrong in practice:

- Webhooks arrive twice, late, out of order, or not at all.
- Payers tap "Pay" twice.
- A payment lands after its booking was cancelled.
- The escrow partner is down when money needs to move.

## Decision

1. **Mirror, don't hold.**
   - `escrow_transactions` mirrors the licensed partner's escrow, one per booking.
   - Payments go through Paystack (NGN) or Stripe (abroad) and fund that escrow.
2. **Double-entry ledger.**
   - Every movement posts one balanced `txn_group` to `ledger_entries`.
   - The accounts are `gateway_clearing`, `family_funds`, `escrow`, `caregiver_payable`
     and `platform_revenue`.
   - Postings are captured, funded, released (with the commission), and refunded.
3. **The database enforces the ledger**, not only the code:
   - a trigger makes `ledger_entries` append-only;
   - a deferred constraint trigger refuses any commit in which a `txn_group` does not
     balance per currency;
   - check constraints require a released or refunded escrow to name its method (`rule` or
     `human`) and, for `human`, the person.
4. **Never trust a webhook.**
   - Check the signature first.
   - Store each delivery once per `(gateway, event_id)`.
   - Confirm the payment by calling the gateway's verify API.
   - The app's `POST /payments/{id}/verify` takes the same code path, so the two can't
     double-credit.
5. **Idempotent everywhere.**
   - The `Idempotency-Key` header on hold.
   - Partner calls keyed by booking id; gateway calls keyed by our payment reference.
   - The same lock order everywhere: booking, then escrow, then payment.
   - If our commit fails after the partner acted, the retry finds the partner done and
     catches up.
6. **Money that can't be placed is flagged, not lost.**
   - This covers late, duplicate and wrong-amount payments.
   - The capture goes on the ledger (in `family_funds`), `needs_review` is set, and an
     incident is opened for a human to refund it.
7. **A dispute holds money even during a partner outage.**
   - Our escrow state is authoritative for release.
   - `partner_status` records the partner's last known state.
   - The settlement sweep brings the partner back in line.
8. **The rule-based release is conservative.**
   - It waits 24 hours after the last check-out, measured by the server clock.
   - It requires every shift checked in and out, inside the geofence, and no open incident.
   - Anything else waits for a human.
9. **Commission** is `PLATFORM_COMMISSION_BPS`, 0 until pricing comes from pilot data.

## Consequences

- When the escrow partner (Topic 3) is chosen, only `app/integrations/escrow.py` changes.
  The partner must support these calls, all idempotent:
  - create, fund, release, refund;
  - open and close a dispute.
- Partial releases (some shifts missed) are a human decision for now. If they become a
  rule, add `amount_released_minor` (ERD section 8).
- Refunding a duplicate or late payment is a manual step at the gateway, tracked by its
  incident. This is acceptable at pilot volumes. Automate it with the gateways' refund APIs
  later.
- The test suite builds its schema from the models, so the same trigger SQL is attached to
  the model (`LEDGER_DDL`) and copied into migration 0009.
