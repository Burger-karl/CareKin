# Ops console API (Day 13)

For the web track's `(ops)` route group. Every endpoint needs an **ops** account
(`Authorization: Bearer <access_token>`): `401` without a token, `403 role_forbidden`
for any other role. Live schemas: `http://localhost:8000/docs` (tag **ops**).

Incidents (the incident queue with response-time clocks) were built on Day 11 and live
under `/incidents`: see the trust endpoints in `/docs`.

| Method | Path | Screen |
|---|---|---|
| GET | `/ops/dashboard?period_days=7` | Dashboard |
| GET | `/ops/caregivers/pending` | Vetting queue |
| POST | `/ops/caregivers/{id}/approve` | Approve / reject button |
| GET | `/ops/facilities/pending` | Facility verification tracker |
| POST | `/ops/facilities/{id}/verify` | Verify / reject listing |
| GET | `/audit-log` | Audit log viewer |

## Caregiver vetting (OP-1, TS-1)

`GET /ops/caregivers/pending?status=&ready_only=false&limit=50&offset=0`

Caregivers in `applied`, `verifying` or `verified`, longest waiting first:

```jsonc
{
  "id": "uuid", "user_id": "uuid", "full_name": "Amaka Nwosu", "phone": "+234803...",
  "tier": "trained_carer", "status": "verifying",
  "applied_at": "...", "waiting_days": 3,
  "guarantor_count": 2,
  "checks": {"nin": "passed", "background": "failed"},   // latest run of each; null = never run
  "certifications": {"total": 1, "pending": 1, "verified": 0, "rejected": 0},
  "ready_to_approve": false,
  "blockers": ["background check failed"]                  // show these to the ops user
}
```

`POST /ops/caregivers/{id}/approve`

```jsonc
{}                                                         // approve
{"certifications": [{"id": "uuid", "status": "verified"}]} // verify documents and approve
{"decision": "reject", "reason": "Guarantors could not be reached"}
```

Approval needs: verification already run (`POST /caregivers/{id}/verify`), **2 guarantors**,
latest **NIN** and **background** checks `passed`, and for `licensed_nurse` a **verified
nursing licence** (it can be verified in the same request). Otherwise `409
caregiver_not_ready` with `details.blockers`; nothing is saved. A caregiver who is not in
the queue any more gets `409 caregiver_not_pending`. Rejecting needs a `reason` (`422`
without one).

## Facility verification (OP-3, FF-3)

`GET /ops/facilities/pending?include_stale=true`

Never-checked listings first, then the longest since their last check. `reasons`:
`unverified`, `no_location`, `location_unconfirmed`, `services_pending`, `stale`
(verified, but not re-checked for `FACILITY_REVERIFY_AFTER_DAYS`, default 90).

`POST /ops/facilities/{id}/verify`

```jsonc
{"decision": "verified"}                       // confirms location (if any) and every pending service
{"decision": "verified", "location_verified": false,
 "services": [{"id": "uuid", "status": "verified"}, {"id": "uuid", "status": "rejected"}]}
{"decision": "rejected", "note": "Number not in service"}   // note required
```

Stamps `last_verified_at` (also on rejection: it records when ops last looked) and
`verified_by`. Returns `{facility, services}`. `409 facility_has_no_location` if asked to
confirm a location that isn't there. A facility reaches the **crisis screen** only when it,
its location and a crisis-capable service are verified.

`last_verified_at` now appears on `GET /facilities/{id}` and on the crisis screen's
nearest facilities.

## Dashboard (OP-1, pilot instrumentation)

`GET /ops/dashboard?period_days=7` (1 to 90). Blocks: `users`, `caregivers`
(`awaiting_decision`), `facilities` (`verification_queue`, `stale`), `bookings`
(`avg_minutes_to_accept`), `pilot` (`caregiver_hours`, `completed_shifts`, `family_spend`
per currency), `escrow` (`held`, `on_hold_dispute`), `incidents` (`open`, `overdue`,
`open_disputes`, `avg_first_response_minutes`), `crisis` (`active`, `in_period`).
Money is in minor units (kobo, cents) with a currency code.

## Audit log (OP-5)

`GET /audit-log?actor_id=&action=&entity_type=&entity_id=&since=&until=&before_id=&limit=50`

Newest first. `action` is exact (`passport.view`) or a prefix ending in `.` (`escrow.`).
Page with `before_id=<next_before_id>` until `next_before_id` is `null`. Each read is
recorded as `audit_log.view`.
