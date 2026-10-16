# Bookings domain: work log and handoff

**Last updated:** 2026-10-16
**Branch:** `feature/patients-domain` (**not committed yet**)
**Path worked on (the only folder changed):** `backend/app/domains/bookings/`

Everything outside this folder was read for context only and not modified, as instructed.
`git status` shows changes only in this folder.

**Sources, in priority order:**
1. **PRD** (CareKin PRD Draft v0.1, 7 Oct 2026). This is the authority. Requirement IDs (BK-3,
   TS-4, …) are quoted throughout this log.
2. `docs/architecture/ERD.md` section 2.5, for column-level detail. Where the ERD and PRD disagree,
   the PRD wins, and each deviation from the ERD is listed in section 4.3.

Anything not required by the PRD is marked **"beyond the PRD"** with the reason.

---

## 1. Status at a glance

| Area | State |
| --- | --- |
| Bookings code (8 files, 17 endpoints) | ✅ Done |
| Fixes R1–R7 (missed shifts, expiry, check-in window, duplicate check-ins, cancelled shifts, notifications, geofence alerts) | ✅ Done and tested |
| Lint (`ruff check`, `ruff format --check`) | ✅ Passes |
| Existing test suite | ✅ 103/103 pass (real PostGIS + Redis in Docker) |
| Bookings tests: PRD/ERD conformance + scenarios + R1–R7 | ✅ **56/56 pass** (see 2.7), but the test files are **not in the repo** (outside my folder) |
| Change scope | ✅ Verified with `git status`: only `backend/app/domains/bookings/` changed |
| **CI pipeline** | ❌ **Will fail** until a migration exists (see 3.1 step 1). **Reproduced:** `alembic upgrade head` creates 0 of the 6 bookings tables, and `alembic check` fails with "New upgrade operations detected". |
| Usable in a real environment | ❌ Needs the migration and the caregivers table (3.1, steps 1 and 3) |
| Housekeeping on a schedule | ⚠️ Code done; needs Celery beat (no Celery app in the project yet). Ops can run it by hand. |
| Payments / escrow | ❌ Separate domain, not started |

---

## 2. What I did

### 2.1 Files

| File | What it holds |
| --- | --- |
| `__init__.py` | Package docstring (escrow lives in payments, per the ERD and PRD 10.1) |
| `models.py` | 6 tables, 6 Postgres enums |
| `schemas.py` | Request and response models with validation |
| `service.py` | Business rules, access control, status flow, audit, notice triggers |
| `router.py` | 17 endpoints under `/api/v1/bookings` |
| `jobs.py` | **New.** Housekeeping: expire stale bookings, close overdue shifts (R1, R2) |
| `notifications.py` | **New.** SMS notices for booking events (R6, R7) |
| `BOOKINGS_LOG.md` | This file |

`main.py` already included `bookings_router`, so nothing outside the folder needed editing.

### 2.2 PRD requirements implemented

| PRD | Requirement | How it's implemented |
| --- | --- | --- |
| **BK-1** | Booking types: hourly, daily, overnight, live-in | `booking_type` enum. Hourly and overnight = 1 shift; daily and live-in = one shift per 24 h. |
| **BK-2** | Escrow released after the shift completes, by rule or human, never AI | Bookings never holds money. It tracks each shift's completion (`completed`, `missed`, `actual_start`, `actual_end`) so payments can release per shift. `mark_paid()` is the hook for payments. |
| **BK-3** | GPS check-in/out (CheckIn, Shift), offline-tolerant | Haversine distance to the service location; flagged beyond 200 m. Device timestamps accepted for offline use; replays return the original (R4). Time window enforced (R3). |
| **BK-4** | Two-way ratings after each shift | One review per reviewer per shift, after check-out. |
| **BK-7** (P1) | Care notes per shift, visible to patient or guardian | Encrypted at rest; caregiver writes; patient side reads if they can see health data; every read audited. |
| **TS-3** | Guardian must approve any caregiver for a minor | Minor's booking starts `pending_guardian`; a guardian with `can_approve_caregivers` decides. |
| **TS-4** | No overnight for a minor's caregiver without a guardian present in the first month | Minors only. Any booking touching 22:00–06:00 Lagos counts as overnight. 30 days from the first completed booking with that caregiver. |
| **TS-5** (P1) | Escrow held during a dispute | `mark_disputed()` hook for the trust domain. |
| **CR-1** | Crisis alert to "any assigned caregiver" | `assigned_caregiver_ids()` hook for the crisis domain. |
| **Section 4** | Patient or guardian books; family abroad funds and views only what is shared | Only the patient or a guardian books, cancels and reviews. Family sees bookings, but not the address unless location is shared. |
| **Section 6** | Audit every money movement and sensitive action | Every write (including the jobs) adds an `audit_logs` row in the same transaction. |
| **Section 6** | Human approves money and caregiver decisions, never AI | No AI-only path: approval needs a guardian's token; money is in payments. |
| **9.1** | Privacy: minimal data; health data encrypted | SMS texts carry no names, addresses or health data. Care notes encrypted. Family location hidden unless shared. |

### 2.3 Fixes R1–R7 and the PRD requirement behind each

| # | Problem | What I did | PRD basis |
| --- | --- | --- | --- |
| **R1** | Nothing marked a shift `missed`, so a booking with a skipped shift never completed and escrow could never be released. | `jobs.py`: 12 h after a shift ends, a shift with no check-in becomes `missed`, and one checked in but never checked out is closed as `completed` with `actual_end = NULL` (missing check-out stays visible). Then the booking settles: **completed** if any shift ran, **cancelled (no-show)** if none did, so payments refunds it. Patient, booker and guardians get an SMS for each missed shift and for a no-show. | **BK-2** (release after shift completion), **BK-3**, **Section 15** risk "caregiver neglects a patient", **Section 6** (rule-based, audited) |
| **R2** | Unanswered and unpaid bookings never expired; an unpaid accepted booking blocked the caregiver's calendar forever. | `jobs.py`: `requested` or `pending_guardian` bookings are cancelled once their start passes; `accepted` bookings not paid within 24 h (or before the start) are cancelled. Shifts are cancelled and both sides told. New column `bookings.accepted_at`. | **Beyond the PRD.** The PRD doesn't specify expiry, but without it BK-2 escrowed bookings and caregiver scheduling (8: matching by schedule) break. The 24 h window is my assumption (decision D9). |
| **R3** | A check-in could be days early or late. | Check-in allowed from 1 h before the shift until it ends; check-out up to 12 h after the end. Judged by the **device** time, so offline check-ins still work. New error `outside_shift_window`. A check-in to a `missed` shift returns `shift_missed`. | **BK-3** (GPS check-in, offline-tolerant). Window sizes are assumptions (D6). |
| **R4** | Duplicate check-ins were only blocked in code; simultaneous retries could store two. | DB unique constraint `check_ins (shift_id, kind)`; the losing request gets the original row back. | **BK-3** offline-tolerant replays; **9.2** integrity |
| **R5** | Cancelling or declining left shifts `scheduled`. | New shift status `cancelled`, set on cancel, decline, guardian rejection and expiry. | **OP-4** live map of shifts (P1); accurate shift data for BK-2 |
| **R6** | No one was told about booking events. | `notifications.py` sends SMS via the existing `app/integrations/sms.py` client: new request → caregiver; approval needed → guardians who can approve; approved → caregiver; rejected → booker; accepted, declined, cancelled, expired → the other side; missed shift and no-show → patient, booker, guardians. **Best effort:** sent after commit, failures only logged, never break the request. | **PRD 10.2 module 4** (notifications: "shift alerts"), **TS-3** (guardian must act, so must be told), **9.1** (no health data in texts) |
| **R7** | Out-of-geofence check-ins were flagged but nobody saw them. | The patient, booker and guardians get an SMS ("checked in about 8.4 km from the booking address"), and there's a new ops queue `GET /bookings/ops/flagged-check-ins`. | **Section 15** risk "caregiver harms or neglects a patient"; **Section 6** "AI can flag; a human decides" (the system flags, ops review); **OP-2/OP-4** ops console |

### 2.4 Endpoints

```
GET    /bookings                                         list mine (?status=&limit=&offset=)
POST   /bookings                                         create (patient or guardian)
GET    /bookings/{id}
POST   /bookings/{id}/accept                             caregiver
POST   /bookings/{id}/decline                            caregiver
POST   /bookings/{id}/cancel                             before payment only
GET    /bookings/{id}/guardian-approvals
POST   /bookings/{id}/guardian-approvals                 TS-3
GET    /bookings/{id}/shifts
GET    /bookings/{id}/shifts/{shift_id}/check-ins
POST   /bookings/{id}/shifts/{shift_id}/check-ins        BK-3
GET    /bookings/{id}/shifts/{shift_id}/care-notes       BK-7
POST   /bookings/{id}/shifts/{shift_id}/care-notes
GET    /bookings/{id}/reviews
POST   /bookings/{id}/shifts/{shift_id}/reviews          BK-4
GET    /bookings/ops/flagged-check-ins                   ops only (R7)
POST   /bookings/ops/housekeeping                        ops only; runs R1+R2 now
```

### 2.5 Status flows

```
Booking:
  pending_guardian -> requested            guardian approves (TS-3)
  pending_guardian -> declined             guardian rejects
  requested        -> accepted | declined  caregiver decides
  requested, pending_guardian, accepted -> cancelled   user cancels, or expires (R2)
  accepted         -> paid                 payments calls mark_paid()
  paid             -> in_progress          first check-in
  paid, in_progress -> completed           no shift left open, at least one ran (R1)
  paid, in_progress -> cancelled           every shift missed = no-show (R1)
  paid, in_progress, completed -> disputed trust calls mark_disputed() (TS-5)

Shift:
  scheduled -> in_progress -> completed    check-in, check-out
  in_progress -> completed (actual_end NULL)   never checked out (R1)
  scheduled -> missed                      no check-in, 12 h after end (R1)
  scheduled -> cancelled                   booking cancelled, declined or expired (R5)
```

### 2.6 How it was verified

- `ruff check` and `ruff format --check` pass; the app imports; OpenAPI lists 17 bookings endpoints.
- **Existing suite: 103/103 pass** (throwaway PostGIS 16 + Redis 7 in Docker).
- **Bookings tests: 16/16 pass** (throwaway files, kept outside the repo):
  - *Scenarios (7):* full adult lifecycle; clash refused; 404 for strangers; geofence flag; replay
    idempotency; care-note encryption and audit; per-shift reviews; guardian approval for a minor;
    TS-4 for minors only (including an "hourly" night booking); family abroad can't book but can
    view with location hidden; live-in split into shifts; `assigned_caregiver_ids`; 503 without the caregivers table.
  - *R1–R7 (9):*
    - R6 right recipients for each event, and no names or addresses in any text
    - R6 SMS provider failure doesn't break booking creation
    - R5 cancel and decline cancel shifts
    - R3 early check-in refused
    - R4 5 simultaneous identical check-ins → all 201, one row
    - R7 geofence SMS sent, ops queue lists it, non-ops refused
    - R2 nothing expires at 12 h; at 25 h both kinds expire with SMS, and the freed slot can be rebooked
    - R1 missed shift → booking completed, plus SMS
    - R1 no-show → cancelled; never-checked-out shift auto-closed; ops endpoint 200 for ops and 403 for others
- **A bug found and fixed while testing:** error details containing dates crashed the error
  handler (`core/exceptions.py` doesn't JSON-encode details). This affected the TS-4 error, latent
  until a completed booking exists, and the new R3 errors. Dates are now sent as ISO strings.

### 2.7 Full conformance test run against the PRD and ERD

Run on 2026-10-16 against PostGIS 16 + Redis 7 (throwaway Docker). **Result: 56/56 bookings
tests pass, and the existing project suite still passes 103/103.** The test files are in the
session scratchpad, not the repo: `test_prd_erd.py` (40 tests), `test_bookings_flow.py` (7),
`test_r1_r7.py` (9). Move them into `backend/app/tests/test_bookings.py` (step 2 below).

**ERD conformance.** Checks the real Postgres schema the models create, not just the Python code.

| ERD item | What the test checks | Result |
| --- | --- | --- |
| 2.5 tables and columns | All 6 tables exist; every column has the ERD's type and nullability (only the documented deviations `accepted_at` and `reviews.shift_id` are extra) | ✅ |
| Conventions: PKs | Every table has `id uuid PRIMARY KEY DEFAULT gen_random_uuid()` | ✅ |
| Conventions: time and money | Timestamps are `timestamptz`; money is `bigint` minor units; API returns integers; price rounds up to a whole kobo, never a float | ✅ |
| Conventions: `encrypted_text` | `care_notes.body` is `bytea` and unreadable in raw SQL | ✅ |
| 5 enums | All 6 enums have the ERD's exact values and order (+ `shift_status.cancelled`, documented) | ✅ |
| 6 foreign keys | All 12 FKs point at the right table with the right delete rule (RESTRICT, or SET NULL for `booked_by` and `author_id`). `caregiver_id` has no FK yet (documented, B2). | ✅ |
| 6 lookup indexes | Every FK column is indexed; `(caregiver_id, starts_at)` exists; GIST on `service_location` | ✅ |
| 6 checks | `starts_at < ends_at`, `rating 1–5`, `scheduled_start < scheduled_end`, money ≥ 0. Postgres itself rejects violations. | ✅ |
| 6 uniques | `reviews (shift_id, reviewer_id)` and `check_ins (shift_id, kind)` (documented deviations) | ✅ |
| 6 deletes | Deleting a patient with bookings, or a booking with shifts, is refused (health data never cascades away) | ✅ |

**PRD conformance.**

| PRD | What the tests check | Result |
| --- | --- | --- |
| BK-1 | All 4 types bookable via the API; shift split for each type is contiguous and covers the whole booking | ✅ |
| BK-2 / §5.3 / §6 | No bookings endpoint pays, releases, refunds or touches escrow; `paid` only from `accepted`; no check-in before payment; no user cancel after payment | ✅ |
| BK-3 | Within 200 m → in geofence; 8.4 km → flagged; offline (past) device time kept and used as `actual_start`; server time recorded separately; device clock ahead refused; check-out before check-in refused; time window (R3); simultaneous duplicates → one row (R4) | ✅ |
| BK-4 | Per shift, only after check-out; reviewee is the caregiver or the booker as appropriate; family and ops can't review; duplicates refused | ✅ |
| BK-7 | Only the caregiver writes; not before the shift starts; patient and family-with-health-share read; family without share and ops refused; encrypted at rest; no note text in the audit log | ✅ |
| TS-3 | Minor → `pending_guardian`; guardian without `can_approve_caregivers`, and the caregiver, can't approve; caregiver can't accept until approved; rejection → `declined`; second decision refused; decision audited; adults (18+) need no approval | ✅ |
| TS-4 | Adults exempt; minors blocked in the first month (completed booking 10 days ago) and allowed after it (40 days ago); a night-time "hourly" booking blocked; `guardian_present` unlocks; night detection unit-tested | ✅ |
| TS-5 | Dispute only after payment; a disputed booking can't be checked in to or cancelled; audited | ✅ |
| CR-1 | `assigned_caregiver_ids` empty before the booking, returns the caregiver during it | ✅ |
| §4 roles | All 6 roles: patient/guardian see and book; caregiver sees only their own; ops sees all but can't book, accept or read care notes; facility admin, other caregivers and strangers get 404 (existence not leaked); ops-only endpoints refuse other roles; no token → 401 | ✅ |
| §6 audit | Create, decline, cancel, accept, paid, check-in, check-out and review all audited with who and when; no address, names, comments or coordinates in audit metadata | ✅ |
| §9.1 privacy | Family sees the address only with a location share; check-in locations hidden likewise; SMS texts contain no names, address, genotype or condition | ✅ |
| §10.2 notices (R6/R7) | Right recipients per event; an SMS provider failure doesn't break a booking | ✅ |
| Input validation | Rejected: zero, under 1 h and over 31 day bookings; missing timezone; unknown type; invalid coordinates; short address; client-set price or status; ratings 0, 6 and −1; bad check-in kind | ✅ |

**Not covered by tests** (and why): the real caregivers table (doesn't exist; a stand-in table
was used); real SMS delivery (mock client); payments and escrow (separate domain); Celery
scheduling (no Celery app). Housekeeping was tested by calling it with a simulated "now".

---

## 3. What I didn't do, and what the next person should do

### 3.1 Next steps, in order

| Step | What | Where | Why / PRD |
| --- | --- | --- | --- |
| **1** | **Write the bookings migration.** CI fails at `alembic check` (`.github/workflows/ci.yml` line 50) until this exists. Run `alembic revision --autogenerate -m "bookings tables"` and check it includes: the 6 tables; enums `booking_type`, `booking_status`, `approval_decision`, `shift_status` (**with `cancelled`**), `checkin_kind`, `review_direction`; `bookings.accepted_at`; unique `check_ins (shift_id, kind)`; unique `reviews (shift_id, reviewer_id)`; the GIST index on `bookings.service_location`; and a downgrade that drops it all. | `backend/alembic/versions/` | Nothing works outside tests without it |
| **2** | **Move the tests into the repo.** `test_bookings.py` is a one-line stub. The 16 throwaway tests can be dropped in (ask for them). Add unit tests for `price_minor`, `split_into_shifts`, `covers_night`, `distance_m`. | `backend/app/tests/test_bookings.py` | **9.2 / 11:** "automated tests for every money-moving code path" |
| **3** | **Build the caregivers model.** Until then, creating a booking and every caregiver action return **503 `caregivers_unavailable`**. Needs `id`, `user_id` (unique), `status` (`approved` = bookable), `hourly_rate_minor`. Then replace the `_caregivers = table(...)` block in `service.py` with the real model, and add FK `fk_bookings_caregiver_id_caregivers` (ON DELETE RESTRICT) in its migration. | `backend/app/domains/caregivers/` | **ON-4, TS-1** |
| **4** | **Schedule housekeeping.** Create the Celery app (PRD 10.1: Celery + Redis) and add a beat entry running `app.domains.bookings.jobs.run_housekeeping` every 5–15 min, with its own DB session and `Notifier(get_sms_client())`. Until then, ops calls `POST /bookings/ops/housekeeping`. | new `app/core/celery.py` (or similar) | R1, R2; **10.1** background jobs |
| **5** | **Payments domain**: Payment, EscrowTransaction, LedgerEntry, WebhookEvent. Call `bookings.service.mark_paid()` on success (caller commits). **Release escrow only for `completed` shifts.** **Refund** missed shifts and no-shows (booking `cancelled` after `paid`, audit `booking.no_show`). A family-abroad user with `family_links.can_fund` pays (Stripe, BK-5). Commission goes to the ledger (`platform_revenue`). If `mark_paid()` raises `booking_invalid_status` because the booking expired first, refund the payment. | `backend/app/domains/payments/` (new) | **BK-2, BK-5, BK-6**, section 12 |
| **6** | **Trust domain:** on an incident with `is_dispute`, call `bookings.service.mark_disputed()`. Consider turning flagged check-ins (R7) and no-shows (R1) into incidents. | `backend/app/domains/trust/` | **TS-2, TS-5, OP-2** |
| **7** | **Crisis domain:** call `bookings.service.assigned_caregiver_ids(db, patient_id)` when fanning out crisis alerts. | `backend/app/domains/crisis/` | **CR-1** |
| **8** | **Update the ERD** to match the code (see 4.3). | `docs/architecture/ERD.md` | Keep docs true |
| **9** | **Seed data:** bookings and shifts in every status, for the demo and the ops console. `backend/seed/*.py` are empty stubs. | `backend/seed/` | **Section 11** "seed data and a simulator (… shifts …)" |
| **10** | **API docs** `docs/api/bookings.md`, next to `docs/api/auth.md`. | `docs/api/` | Section 14.2 deliverables |

### 3.2 Not done, and why

| Not done | Reason | PRD |
| --- | --- | --- |
| Migration | `backend/alembic/` is outside my folder | — |
| Tests in the repo | `backend/app/tests/` is outside my folder | 9.2 |
| Celery schedule for housekeeping | No Celery app exists, and it would live outside my folder | 10.1 |
| **Family-abroad notifications** | The PRD says family "receives updates and alerts", but it must respect the patient's `share_scope` (ON-5), and what they may receive isn't defined. Notices go to the patient, booker and guardians only. | Section 4, ON-5 |
| **WhatsApp and push** | Only SMS exists (`app/integrations/whatsapp.py` is a stub). The PRD says WhatsApp is the main channel. Swap the client inside `notifications.Notifier` when the notifications module (10.2 #4) exists. Texts are sent inside the request today; move them to a Celery queue. | 10.1, 10.2 |
| Escrow, payments, ledger, refunds | Separate payments domain (ERD and PRD 10.1) | BK-2, BK-5, BK-6 |
| Caregiver availability windows checked at booking | Caregivers domain doesn't exist; only booking clashes are checked | ON-4, 8 (matching) |
| Caregiver `avg_rating` update after reviews | Columns live in the caregivers domain | BK-4 |
| Ops actions (force cancel, refund, resolve) | Ops can read, see flagged check-ins and run housekeeping; actions belong with payments and the ops console | OP-2 |
| Ops approval for minors | TS-3 says guardian (see D2) | TS-3, 8 |
| Matching agent | Separate AI component. It can create bookings via `POST /bookings` with the patient's or guardian's token, and can't approve or pay. | 8 |
| Insurance (TS-6), certification fees (CA-3) | P2/P1, other domains | TS-6, CA-3 |

### 3.3 Known limitations in what was built

- **Late offline sync:** a check-in that reaches the server more than 12 h after its shift ended
  finds the shift already `missed` and is refused (`shift_missed`). Ops would need to fix it by hand.
  Raise `LATE_GRACE` if caregivers are often offline that long.
- **Auto-closed shifts** (`completed`, `actual_end` NULL) count as worked. Payments or ops should
  decide whether to pay a shift with no check-out.
- Booking lists return no total count (add one if the apps need paging UI).
- Bookings imports helpers from patients (`EncryptedText`, `GeoPoint`, and the private `_to_wkt`
  and `_from_wkb`). Move them to `app/core/encryption.py` and a new `app/core/geo.py`. Each
  import has a `TODO`.
- SMS is sent inside the request, so a slow SMS provider slows the request (it never fails it).

---

## 4. Decisions and deviations

### 4.1 Decisions the team needs to make (defaults in use)

| # | Question | Built today | Change in |
| --- | --- | --- | --- |
| D1 | TS-3: approve **each booking**, or **a caregiver once**? ("approve any caregiver") | Each booking (stricter) | `create_booking` |
| D2 | Can **ops** approve for minors? (Section 8 says "guardian or operations"; TS-3 says guardian) | Guardian only | `decide_guardian_approval` |
| D3 | Pricing per booking type (PRD 16: set from field data) | Hourly × duration, NGN | `price_minor` |
| D4 | TS-3 for guardian-managed **adults** with no login? | Yes | `_needs_guardian` |
| D5 | TS-4 night window, "first month" | 22:00–06:00 Lagos; 30 days from the first completed booking | `NIGHT_START`, `NIGHT_END`, `FIRST_MONTH` |
| D6 | Geofence radius; check-in window | 200 m; 1 h early; check-out up to 12 h late | `GEOFENCE_RADIUS_M`, `CHECK_IN_EARLY`, `LATE_GRACE` |
| D7 | Store a reason on decline or cancel? | No (no column; free text stays out of audit metadata) | model + schema |
| D8 | Cancel after payment? | Refused; ops and payments | `cancel_booking` |
| D9 | How long to pay after acceptance? (R2) | 24 h | `PAYMENT_WINDOW` |
| D10 | When does a shift with no check-in count as missed? (R1) | 12 h after its end | `LATE_GRACE` |
| D11 | All shifts missed: cancel and refund, or open a dispute for ops? (R1) | Cancel (no-show), audited `booking.no_show` | `settle_booking` |

### 4.2 Other defaults

Booking length 1 h to 31 days, starting in the future; device clock skew 5 min; currency NGN
(family abroad converts in payments); ops can read bookings but not care notes.

### 4.3 Deviations from the ERD (update `docs/architecture/ERD.md`)

| Change | Why |
| --- | --- |
| `reviews.shift_id` added; unique `(shift_id, reviewer_id)` replaces `(booking_id, reviewer_id)` | PRD BK-4 "after each shift" |
| `bookings.booked_by` is "patient or guardian" (ERD says "or family member") | PRD section 4 |
| `bookings.accepted_at` added | R2 expiry (beyond the PRD) |
| `shift_status` gains `cancelled` | R5 |
| Unique `check_ins (shift_id, kind)` | R4 (BK-3 offline replays) |

---

## 5. How to run locally

```bash
docker compose up -d db redis                 # from the repo root
docker compose exec db createdb -U carekin carekin_test
cd backend
pytest                                        # TEST_DATABASE_URL / TEST_REDIS_URL to override
ruff check app/domains/bookings && ruff format --check app/domains/bookings
```
With `USE_MOCK_INTEGRATIONS=true`, SMS notices go to the in-memory mock (`app/integrations/sms.py`)
and are only logged, with masked numbers. The `/docs` page lists all bookings endpoints.
