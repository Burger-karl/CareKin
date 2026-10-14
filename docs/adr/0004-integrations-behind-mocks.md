# ADR 0004: Every partner behind an interface with a deterministic mock

- **Status:** Accepted
- **Date:** 2026-10-14
- **Owner:** Backend
- **PRD refs:** 6 (trust and safety), 9.2, 10.1; build plan Day 3

## Context

CareKin depends on partners that aren't chosen or contracted yet: NIN verification and
background checks (Topic 6) and the licensed escrow partner (Topic 3). Others are chosen
but need accounts and approvals: SMS (Termii sender ID), WhatsApp (Meta Business
verification), Paystack, S3. Feature days (6 to 15) can't wait for any of them.

## Decision

1. Each partner has a module in `app/integrations/` (S3 in `app/core/storage.py`) with:
   - a `Protocol` describing the calls the app makes;
   - a **mock** that is deterministic. Documented inputs produce each outcome, e.g. a
     NIN ending in 0 is "not found" and ending in 9 is a provider outage, so every path
     can be tested and demoed;
   - a real client where the provider is settled: Termii SMS, WhatsApp Cloud API,
     Paystack, S3;
   - a `get_<name>_client()` factory used as a FastAPI dependency, so tests override it.
2. `USE_MOCK_INTEGRATIONS=true` selects every mock. With it false, a partner without a
   provider or keys answers **503 `<name>_not_configured`**. It never silently falls
   back to a mock.
3. Production refuses mock SMS (crisis alerts would reach nobody). Other mocks are
   allowed for the pilot, but each pilot environment must list which ones are mocked.
4. Partner failures raise `IntegrationError` (with `retryable`). Clients never log
   personal data or secrets: phone numbers are masked, and NIN results keep the last 4
   digits only.
5. Status values match the ERD enums (`escrow_status`, `payment_status`, `check_status`),
   so feature code stores them without mapping.

## Consequences

- When Topic 3 or 6 is decided, only the real client and the factory change. Feature
  code and tests don't.
- Mocks hold state in process memory (escrow, payments). That's fine for development and
  tests, but staging must use real sandboxes before Gate 2.
- Webhook routes (Paystack, WhatsApp) verify signatures the same way in mock and real
  mode, so the verification code is tested.
