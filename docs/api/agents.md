# Matching agent API contract (Day 14)

For the web and mobile tracks. Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
Live, always-current schemas: `http://localhost:8000/docs`.

The family describes who they want in their own words; the agent ranks caregivers and
explains why. **The agent never books**: book with `POST /bookings` as usual. It never gives
medical advice: an emergency or a medical question is handed to a person instead.

## It's slow: plan the UI for it

The AI runs on our own server (health data never leaves it), on CPU. A request takes
**about 20–45 seconds** (the first one after a quiet spell is the slowest). Show a
"finding caregivers…" state, keep the user's message on screen, and set the client
timeout to **at least 2 minutes**. Don't retry automatically: each retry is another wait.

## Flow

1. The user types what they want ("someone who speaks Igbo, good with children").
2. `POST /agents/match` with that `message` **and the booking the app already has**:
   patient, booking type and time slot. The AI only reads `message` for preferences
   (language, tier); the patient and time always come from these fields.
3. Show `reply`, then the ranked `results` (same shape as `POST /caregivers/match`).
4. To refine ("only a nurse"), send the next message with the `session_id` you got back.
5. The user picks a caregiver: `POST /bookings` as usual.

If `status` is `handed_off`, **don't show results**: show `reply` (our fixed message) and
the Crisis button prominently. That conversation is closed to the agent; a person from the
CareKin team follows up. To search again, start a new conversation (no `session_id`).
Ops read a handed-off conversation with `GET /agents/companion/history/{session_id}`
(see [companion.md](companion.md)); it works for matching conversations too.

## Who can use it

| | Use the agent |
|---|---|
| Patient (P) | yes, for themselves |
| Guardian (G) | yes, for their patient |
| Ops (O) | yes |
| Family abroad (F), Caregiver (C), Facility | no (403 `role_forbidden`) |

The user needs **`llm_processing` consent** (their messages are read by the AI). Without it:
403 `consent_required`; send them to `POST /auth/consent` with `consent_type: "llm_processing"`.

## Endpoint

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/agents/match` | `AgentMatchRequest` | 200 `AgentMatchOut` |

`AgentMatchRequest`:
```json
{
  "message": "Someone who speaks Igbo, please",
  "patient_id": "3f2a…",
  "booking_type": "hourly",
  "starts_at": "2026-11-02T09:00:00+01:00",
  "ends_at": "2026-11-02T13:00:00+01:00",
  "service_location": null,
  "limit": 5,
  "channel": "web",
  "session_id": null
}
```
- `message`: 1–1000 characters.
- `starts_at` / `ends_at`: with a timezone; 1 hour to 31 days; must be in the future.
- `service_location`: `{latitude, longitude}`; defaults to the patient's home location.
- `limit`: 1–10 caregivers (default 5). `channel`: `web` or `mobile`.

`AgentMatchOut`:
```json
{
  "session_id": "9b1c…",
  "status": "answered",
  "reply": "Ngozi Eze is the best fit: she speaks Igbo and has a 4.9 rating from 21 families.",
  "reply_source": "model",
  "results": [{ "caregiver_id": "…", "full_name": "Ngozi Eze", "tier": "companion", "...": "..." }],
  "guardian_approval_required": false,
  "min_tier_applied": null
}
```
- `status`: `answered`, or `handed_off` (see Flow).
- `reply_source`: `model` (written by the AI), `fallback` (written from the results because
  the AI was unavailable or off-topic: still correct, just plainer), or `handoff`.
- `results`: best first. Each has the public profile fields plus `distance_km`,
  `estimated_total_minor` (kobo), `score` and `factors`. Empty after a handoff, or when
  nobody is free (the `reply` says so).
- `guardian_approval_required`: a booking will wait for a guardian's approval (TS-3). The
  `reply` already ends with a note saying so.
- `min_tier_applied`: the lowest tier searched. Patients under 18 always get
  `trained_carer` or above.

## Errors

All of these come back fast, before the AI runs.

| Status | `code` | Meaning |
|---|---|---|
| 400 | `slot_in_past` | The time slot has started |
| 401 | `not_authenticated` | No or bad token |
| 403 | `consent_required` | No `llm_processing` consent (see above) |
| 403 | `role_forbidden` | This role can't use the agent |
| 403 | `patient_forbidden` | Linked to this patient only as family: only the patient or a guardian can search |
| 404 | `patient_not_found` | No such patient, or no access to them |
| 404 | `conversation_not_found` | `session_id` isn't yours |
| 409 | `conversation_handed_off` | That conversation is with a person now; start a new one |
| 409 | `conversation_patient_mismatch` | `session_id` belongs to another patient |
| 422 | | Invalid body (e.g. empty `message`, slot too short) |
