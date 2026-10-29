# ADR 0006: NIN verification through Dojah

- **Status:** Accepted
- **Date:** 2026-10-29
- **Owner:** Backend
- **PRD refs:** TS-1 (layered vetting), ON-4; open decision Topic 6

## Context

Ops must check every caregiver's NIN before approving them (TS-1). Until now only the
mock in `app/integrations/nin.py` existed (ADR 0004). The team chose Dojah. What we found
on 29 October 2026:

- **No free plan for live checks.** Live lookups are charged to a prepaid Dojah wallet.
  The pricing page says "as low as $0.06 per API call" on the Starting Out plan; the NIN
  price itself comes from Dojah sales.
- **Live keys need compliance approval:** "You can switch to Live only after compliance
  is approved."
- **The sandbox is free:** "Sandbox calls never draw from your wallet." It returns
  simulated records, with the same request and response shapes as live. Its test NIN is
  `70123456789`.
- The lookup is `GET /api/v1/kyc/nin?nin=...`, with headers `AppId` and `Authorization`
  (the raw secret key, no `Bearer`). It returns the NIMC record: names, date of birth,
  phone, photo. 404 means no record; 402 means the wallet is empty.
  It takes no name to compare, so we compare names ourselves.

## Decision

1. `DojahNINClient` implements the existing `NINClient` Protocol, so the caregiver
   service and its API don't change.
2. **Name match**, done by us:
   - The check passes when the record's first and last names are both in the
     caregiver's account name, in any order, ignoring case, accents and punctuation.
   - It also passes when every word of a two-or-more-word account name is in the record
     and includes the surname, for people who use their middle name.
   - Anything else fails, and ops decides by hand.
3. **Outcomes:**

   | Dojah answer | Check result | Retry? |
   | --- | --- | --- |
   | 200, names match | `passed` | |
   | 200, names differ | `failed` | |
   | 404 | `failed` (no record) | |
   | 400, 401, 402 | `error` | No: fix the request, keys or wallet |
   | 424, 429, 5xx, network | `error` | Yes |

4. **Data:**
   - We keep only the outcome, the last 4 digits and a reference. The reference is an
     HMAC of the NIN keyed with `SECRET_KEY`: an unkeyed hash of an 11-digit number can
     be reversed by trying every number.
   - The record (photo, date of birth, phone) is read for the name match and dropped.
   - httpx's request log (which prints `?nin=...`) is switched off for every partner
     call.
5. **Choosing the provider:**
   - `NIN_PROVIDER` is `mock` or `dojah`. Unset, it follows `USE_MOCK_INTEGRATIONS`.
   - This lets a demo call the free sandbox while payments and escrow stay mocked.
   - `DOJAH_BASE_URL` defaults to the sandbox. Production refuses to start with the
     sandbox, or with `dojah` and no keys.

## Consequences

- Staging can run real Dojah calls for free, against simulated records.
- Going live needs:
  - Dojah compliance approval;
  - a funded wallet;
  - `DOJAH_BASE_URL=https://api.dojah.io` with the live keys;
  - a data-processing agreement with Dojah (NDPA): NINs leave CareKin for Dojah and NIMC.
- Watch for 402 errors: when the wallet runs dry, every NIN check is recorded as `error`
  until it's funded. The app logs it at ERROR level.
- A name mismatch isn't proof of fraud (spelling, married names). It stops automatic
  approval, and ops reviews the caregiver.
