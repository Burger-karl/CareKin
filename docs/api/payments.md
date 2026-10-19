# Payments, escrow and incidents API contract (Day 11)

For the web and mobile tracks. Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
Live, always-current schemas: `http://localhost:8000/docs`.

CareKin never holds money. The family pays through Paystack (in Nigeria) or Stripe (from
abroad). The money goes into an escrow at a licensed partner. It is released to the
caregiver after the care is done, or refunded to the payer. Amounts are integers in kobo:
`600000` means NGN 6,000.00.

## Paying for a booking

1. The caregiver accepts the booking, so its status is `accepted`. It must be paid within
   24 hours and before it starts.
2. Make one `Idempotency-Key` (UUID v4) **per tap on "Pay"** and keep it. Send
   `POST /bookings/{id}/escrow/hold` with `{"gateway": "paystack"}`, or
   `{"gateway": "stripe"}` for family abroad.
   - `201`: a new checkout. `200`: the same checkout again, from a retry or a double tap.
   - Open `authorization_url` in a browser or webview.
3. The gateway sends the payer back to `PAYMENT_RETURN_URL?booking=<id>`. Then call
   `POST /payments/{payment_id}/verify`:
   - `funded`: paid, and the booking is now `paid`.
   - `pending`: not paid yet. Poll every few seconds, or wait.
   - `failed`: declined or expired. Start again from step 2 with a **new** Idempotency-Key.
   - `needs_review`: money arrived but couldn't be used (the booking was cancelled in the
     meantime, or paid twice). CareKin operations will refund it. Show
     "We'll contact you about a refund".
   - `funding_retry`: the payment worked, and the escrow partner will be updated shortly.
     Show "Payment received, confirming...".
4. You don't need to wait for step 3. The gateway's webhook confirms the payment too, and
   both paths are safe to run together.

`GET /bookings/{id}/escrow` shows the escrow state and the payments made:

| Escrow state | What it means |
| --- | --- |
| `pending` | Waiting for payment. |
| `held` | Paid; the money is held. |
| `on_hold_dispute` | Someone opened a dispute, so the money can't be released. |
| `released` | Paid out to the caregiver. |
| `refunded` | Returned to the payer. |

## Releasing payment to the caregiver

- **The family confirms.** When the booking is `completed`, the patient or a guardian can
  call `POST /bookings/{id}/escrow/release`. Show this as "Confirm care and pay the
  caregiver".
- **Automatic release.** CareKin also releases payment on its own 24 hours after the last
  check-out, if the booking was clean:
  - every shift was checked in and out;
  - every check-in and check-out was inside the geofence;
  - no incident is open on the booking.
- **Ops.** Operations can release any completed booking.
- **Refused while a dispute is open:** `409 dispute_open`.

## Disputes and incidents

| Method | Path | Who | Notes |
| --- | --- | --- | --- |
| POST | `/incidents` | anyone signed in | `{category, severity?, description, booking_id?, is_dispute?, against_user_id?}`. Booking needed for a dispute. |
| GET | `/incidents` | ops: the queue; others: their own reports | `?status=&booking_id=&disputes_only=` |
| GET | `/incidents/{id}` | reporter, the person it's about, ops | |
| PATCH | `/incidents/{id}` | ops | `{status?, severity?, assigned_to?, resolution?, is_dispute?: true}` |

- `is_dispute: true` holds the booking's payment. The booking becomes `disputed`.
- Who can open a dispute: the patient, a guardian, family allowed to fund care, the
  caregiver, or ops. It can only be opened while the payment is held.
- Closing the last dispute (`resolved` or `dismissed`, which needs a `resolution`) gives
  the booking back its previous status. To settle a dispute for the family, ops refunds
  first, then closes it.
- Response targets (TS-2): critical 1 h, high 4 h, medium 24 h, low 72 h.

## Who can do what

| | Pay (hold) | Verify | View escrow | Release | Refund | Ledger |
| --- | --- | --- | --- | --- | --- | --- |
| Patient / guardian | yes | yes | yes | yes, once completed | no | no |
| Family abroad with `can_fund` | yes | yes | yes | no | no | no |
| Family without `can_fund` | no | yes | yes | no | no | no |
| Caregiver | no | no | yes (no payment list) | no | no | no |
| Ops | no | yes | yes | yes | yes | yes |

## Endpoints

| Method | Path | Success |
| --- | --- | --- |
| POST | `/bookings/{id}/escrow/hold` | 201/200 `CheckoutRead` |
| GET | `/bookings/{id}/escrow` | 200 `BookingMoneyRead` |
| POST | `/bookings/{id}/escrow/release` | 200 `EscrowRead` |
| POST | `/bookings/{id}/escrow/refund` (ops, `{reason}`) | 200 `EscrowRead` |
| GET | `/bookings/{id}/ledger` (ops) | 200 `LedgerEntryRead[]` |
| POST | `/payments/{payment_id}/verify` | 200 `PaymentVerifyRead` |
| POST | `/payments/ops/settle` (ops) | 200 `SettlementRead` |
| POST | `/payments/webhooks/paystack` | gateway only, signed |
| POST | `/payments/webhooks/stripe` | gateway only, signed |

## Errors you should handle

| Code | Status | Show |
| --- | --- | --- |
| `booking_not_payable` | 409 | The booking can't be paid in its current state. |
| `booking_already_paid` | 409 | Already paid; refresh the booking. |
| `payment_window_closed` | 422 | Too late to pay; book again. |
| `funding_not_allowed` | 403 | The patient hasn't allowed this family member to pay. |
| `idempotency_key_reused` | 409 | A bug: make a new key per tap. |
| `dispute_open` | 409 | Payment is on hold while CareKin reviews a dispute. |
| `booking_not_completed` | 409 | Release is possible after the last check-out. |
| `provider_unavailable`, `escrow_partner_unavailable`, `payments_not_configured` | 503 | Try again shortly. |

## Setting up webhooks (ops, once per environment)

- **Paystack:** Dashboard → Settings → API Keys & Webhooks. Set the URL to
  `https://<api host>/api/v1/payments/webhooks/paystack`. The signature uses
  `PAYSTACK_SECRET_KEY`.
- **Stripe:** Developers → Webhooks. Point it at `.../payments/webhooks/stripe` and send
  these events:
  - `checkout.session.completed`
  - `checkout.session.async_payment_succeeded`
  - `checkout.session.async_payment_failed`
  - `checkout.session.expired`

  Put the signing secret in `STRIPE_WEBHOOK_SECRET`.
- **Celery beat** runs `payments.settle_escrows` every 10 minutes. It recovers lost
  webhooks, retries escrow funding, refunds no-shows, releases clean bookings, and catches
  the partner up after an outage. Ops can also run it with `POST /payments/ops/settle`.
