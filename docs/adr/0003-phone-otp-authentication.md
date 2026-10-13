# ADR 0003: Phone OTP authentication with rotating refresh tokens

- **Status:** Proposed
- **Date:** 2026-10-13
- **Owner:** Auth and onboarding
- **PRD refs:** ON-1, ON-2

## Context

Users sign up with a phone number (ON-1). Many target users in Enugu share devices,
use low-end Android phones and are unlikely to manage passwords. Health data must not
be stored before consent is recorded (ON-2). The kickoff lists separate endpoints for
OTP verify and login, and the ERD models users, refresh tokens and consent records.

## Decision

1. **Passwordless.** Every sign-in is a 6-digit SMS code. No password column exists.
   - `otp/verify` confirms a newly registered phone and signs the user in.
   - `login` signs in a verified user with a code from `otp/send`.
2. **Codes live in Redis**, stored as an HMAC (keyed with `SECRET_KEY`) for 5 minutes,
   single-use, burned after 5 wrong attempts. Sending is limited to one per 60 s and
   5 per hour per number, and 30 per hour per IP.
3. **Access tokens** are HS256 JWTs valid for 30 minutes (PyJWT, replacing the
   unmaintained python-jose; passlib removed as unused).
4. **Refresh tokens** are opaque random strings valid for 14 days, stored only as
   SHA-256 hashes, and rotated on every use. Presenting an already-rotated token
   revokes every session for that user.
5. **Roles** use the ERD enum (`patient, guardian, family_abroad, caregiver,
   facility_admin, ops`), one per account. Self-registration allows patient, guardian,
   caregiver and facility_admin. The kickoff letters map to these values in
   `core/permissions.py`.
6. **Consent** is an append-only history. The current policy version is a setting;
   bumping it makes `/auth/me` ask for consent again. `require_consent()` guards every
   endpoint that stores health data.
7. **SMS** goes through a mock until a provider is chosen. In local development only,
   the code is also returned as `debug_code`.

## Consequences

- Each login costs one SMS. Budget accordingly and choose a provider with good
  delivery on MTN, Airtel, Glo and 9mobile.
- A user who loses their SIM loses access until support verifies them. A phone-change
  flow is not built yet.
- One role per account: a guardian who is also a patient needs two accounts. Revisit
  after interviews (ERD section 8).
- Refresh reuse detection can log out a user whose app retries a refresh after a
  dropped response on a bad network. Clients must queue requests behind a single
  refresh. If pilot users hit this, add a short grace window.

## Open

- SMS provider (Termii, Africa's Talking, Twilio).
- Whether WhatsApp should also deliver OTPs (cheaper, and the companion uses it).
