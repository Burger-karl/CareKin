# CareKin build log

## Day 12: GPS check-in, check-out and reviews (Gate 2)

Date: 2026-10-16
Plan reference: CareKin Daily Endpoint Plan, Day 12 (PRD BK-3, BK-4)
Branch: `gps-check-in-check-out-and-reviews` (cut from `development`)

> Status at the end of the day, in one line: the backend is written and waiting for its
> first test run; the web and mobile screens are not started; releasing escrow after
> check-out is deliberately left out until Day 11 exists.

---

### 1. The task

The plan asks for three endpoints, plus screens on web and mobile:

| Plan item | What it should do |
|---|---|
| `POST /bookings/{id}/checkin` | Geofence check; keep the device's timestamp and the server's receipt time |
| `POST /bookings/{id}/checkout` | End the shift; trigger rule-based release of escrow or queue it for ops |
| `POST /bookings/{id}/review` | Two-way rating: families rate caregivers and caregivers rate families |
| Web | `CheckInMap` and a review form |
| Mobile | `checkin.tsx` with GPS and offline queueing |

Done when (Gate 2): the full flow runs on staging: onboard, find facility, crisis,
passport, book, pay, check in, check out, release.

---

### 2. First job: understand what already exists

Before writing anything I went through the repo (the zip of my clone) to see what was
already there. This is what I found.

**Already done by others**

- Day 6 (my caregiver work) is merged into `development` as PR #5, so that task is finished.
- The bookings domain (Day 10, by Ozioko Anthony) already contains most of the logic Day 12
  describes, but under different URLs:
  - check-in and check-out: `POST /bookings/{id}/shifts/{shift_id}/check-ins`, with a `kind`
    of `check_in` or `check_out`. It already handles the 200 m geofence (recorded but flagged
    when outside), the device timestamp plus server receipt time, offline replays, a rule
    that a check-in is only accepted from one hour before the shift, and an ops list of
    flagged check-ins.
  - reviews: `POST /bookings/{id}/shifts/{shift_id}/reviews`, already two-way
    (`family_to_caregiver` and `caregiver_to_family`).

**Gaps against the Day 12 plan**

1. The plan's URLs (`/checkin`, `/checkout`, `/review` on the booking itself) did not exist.
2. Nothing updated a caregiver's `avg_rating` and `rating_count`. We created those columns on
   the caregiver model on Day 6 for exactly this.
3. Check-out marks the booking `completed` but nothing releases escrow, because there is no
   payments domain yet (Day 11 is not in the repo; only the provider clients exist in
   `integrations/`).
4. `test_bookings.py` was a one-line stub, so none of the booking behaviour had tests.
5. The web and mobile files are empty placeholders: `CheckInMap.tsx`, `checkin.tsx`,
   `services/api.ts`, `services/offline.ts` all contain a comment or `return null`. There is no
   API client, no login handling, and the dependencies (map library, GPS, storage) are not
   declared in either `package.json`.

---

### 3. Decisions made today

| Question | Decision | Made by |
|---|---|---|
| Add the plan's booking-level URLs, or accept the shift-scoped ones as Day 12? | Build everything missing, so add the plan's URLs on top of the existing logic | Loveth |
| What about releasing escrow on check-out? | Leave it out for now; build everything else | Loveth |
| Backend only, or web and mobile too? | The plan lists web and mobile for Day 12, so build them too, in one go | Loveth |
| Web map library and mobile GPS/offline libraries | Proposed: Leaflet + OpenStreetMap (no API key); `expo-location`, AsyncStorage, NetInfo. **Not confirmed yet.** | Open |

---

### 4. What was built (backend)

Three new files and two small edits. I deliberately kept the new logic in new files and
touched Anthony's files as little as possible, so there is less to conflict at merge time.

**New: `domains/bookings/booking_level.py`**

The existing service works one shift at a time. A phone usually knows only the booking, so
this module chooses the right shift and then hands over to the existing
`service.record_check_in` / `service.create_review`. Every rule (geofence flagging, access,
audit, offline timestamps) therefore still lives in one place instead of being copied.

How the shift is chosen:

- **Check-in:** the first shift whose window contains the device time (one hour before the
  start until the end). Using the device time, not the server time, means an offline check-in
  is judged by when it happened.
- **Check-out:** the shift that is currently in progress.
- **Resends:** resending a check-in finds the same shift; resending a check-out with the same
  device timestamp finds the shift that already has it. The service then returns the original
  record. This matters for a phone that retries after being offline: without it, a retry on a
  multi-shift booking could jump to the next shift.
- **Review:** the shift in `shift_id` if one is given, otherwise the latest completed shift the
  person has not rated yet. If nothing is completed it says so; if everything is rated it
  returns a conflict.

**New: `domains/bookings/ratings.py`**

`refresh_caregiver_rating()` recomputes a caregiver's average and count from the `reviews`
table (family-to-caregiver reviews only) and stores them on the caregiver. It recomputes
instead of adding to a running total, so the summary cannot drift from the rows it describes.
It locks the caregiver row first, so two reviews arriving at the same moment are counted one
after the other and neither is lost.

**Edit: `domains/bookings/service.py`** (Anthony's file): one import plus two lines at the end of
`create_review` that call the function above. Doing it there, and not only in the new route,
means the old shift-scoped review endpoint updates the average too.

**Edit: `domains/bookings/router.py`** (Anthony's file): the three new routes, added at the
very end of the file.

**New: `tests/test_check_in_and_reviews.py`**

24 test functions (26 test cases, because one is parametrised over three bad ratings). They
insert bookings and shifts directly and then call the real endpoints with real access tokens.
They cover:

- check-in inside and outside the geofence (flagged, and visible in the ops list)
- offline check-in keeping the device time next to the server receipt time
- resent check-ins and check-outs returning the same record
- future device times, check-in before the window opens, unpaid bookings
- who may check in: the booked caregiver only; the patient gets 403, a stranger gets 404
- check-out completing the shift and the booking, and check-out before check-in
- a two-shift booking where each shift is picked by time
- reviews in both directions, the average after two ratings (5 and 4 gives 4.50), and the shift
  route also updating the average
- no review before check-out, no double review, rating limited to 1 to 5, ops and strangers refused

---

### 5. Not done today

- **Release of escrow after the last check-out.** It belongs to the payments domain (Day 11),
  which is not in the repo. Check-out currently stops at marking the booking `completed`.
  The function docstring in `booking_level.py` says where release hooks in.
- **Web:** `CheckInMap` and the review form.
- **Mobile:** `checkin.tsx` with GPS and offline queueing.
- **`test_bookings.py`** (the money-path tests) is Day 11's file and was left alone.

---

### 6. What broke, and what is still open

**The first test run failed to find the new test file**

I ran `python -m pytest app/tests/test_check_in_and_reviews.py -q` in the codespace and got
`ERROR: file or directory not found`. Cause: the new files had not been copied into the
codespace yet, and the codespace was still on the old Day 6 branch (the status bar still
showed `caregiver-onboarding-and-verification`). Nothing was wrong with the code. The
plain `pytest -q` that followed passed 75 tests, which is the existing suite as it was before
Day 12. Fix: create the Day 12 branch from `development`, copy the files in, and run again.
**This is still to do.**

**Nothing has been executed yet**

The environment I wrote the code in cannot run the real libraries (SQLAlchemy, FastAPI,
PostGIS), so the Day 12 code was only syntax-checked. It has not passed a test run. Until
the new tests run in the codespace, treat every "works" in this log as "written to work".

**Things I noticed while reading the repo (not bugs in Day 12)**

- The zip of my clone showed every file as modified and `.github/workflows/ci.yml` as deleted.
  That is Windows line endings and a folder the zip left out, not real changes. Before
  committing, run `git status` and only add the files I mean to.
- Anthony's `create_review` and `router.py` are shared files. The edits are small and
  additive, but he should be told so we do not both change the same lines.

---

### 7. Next steps

1. Create the branch from `development`, add the three new files, make the two small edits.
2. Run `python -m pytest app/tests/test_check_in_and_reviews.py -q`, then the full suite, and
   fix whatever fails.
3. Commit, push, open the pull request into `development`.
4. Confirm the web and mobile library choices, then build `CheckInMap`, the review form,
   `checkin.tsx`, and the API and offline-queue helpers they need.
5. When Day 11 (payments and escrow) lands, add the rule-based release or ops queue after the
   last check-out.

---

### 8. Notes for the pull request description

- Adds the plan's booking-level `checkin`, `checkout` and `review` routes on top of the
  existing shift-scoped service.
- Caregiver `avg_rating` and `rating_count` now update on every family-to-caregiver review.
- Release of escrow after check-out is not included; it waits for the payments domain (Day 11).
- Small additive edits to `bookings/service.py` and `bookings/router.py`: please review.