# Crisis button API contract (Day 8)

For the web and mobile tracks. Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
Live, always-current schemas: `http://localhost:8000/docs`.

Every response carries `disclaimer` and `emergency_number` (PRD CR-3). Show both on every crisis screen.
CareKin never gives treatment advice on these screens.

## Flow

1. User presses the button. The app makes a `client_event_id` (UUID v4) **once per press** and stores it.
2. `POST /crisis/events` with the patient, the `client_event_id` and the GPS fix if there is one.
   - `201` new crisis, `200` the same press again or a crisis already active (nobody is alerted twice).
   - The response already has `alerts` (all `queued`) and `nearby` facilities. Show the facilities at once.
3. Poll `GET /crisis/events/{id}` every few seconds to show each alert going `queued → sent` (or `failed`).
4. If any alert is `failed`, offer "Resend": `POST /crisis/alert`. Also use it after adding a contact mid-crisis.
5. When it's over: `POST /crisis/events/{id}/resolve`, optionally adding it to the health passport.

**Offline:** keep the press (with its `client_event_id` and `triggered_at`) and send it when the
connection returns. Retrying with the same `client_event_id` is always safe.
If `no_contacts` is `true`, tell the user nobody was alerted and to call `emergency_number`.

## Who can do what

| | Press | View / facilities | Resolve / resend |
|---|---|---|---|
| Patient (P) | yes | yes | yes |
| Guardian (G) | yes | yes | yes |
| Family abroad (F) with `location` shared | no | yes | no |
| Ops (O) | no | yes | yes |
| Caregiver (C) | no | no | no |

The person who pressed is never texted about their own press. Guardians who turned off
`receives_crisis_alerts` are skipped. A phone number is texted once even if it's listed twice.

## Endpoints

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/crisis/events` | `{patient_id, client_event_id, location?, triggered_at?}` | 201/200 `CrisisTriggerOut` |
| GET | `/crisis/events/{id}` | | 200 `CrisisEventOut` |
| GET | `/crisis/events/{id}/facilities` | | 200 `FacilitiesOut` |
| POST | `/crisis/events/{id}/resolve` | `{false_alarm?, outcome_facility_id?, add_to_passport?, note?}` | 200 `CrisisResolveOut` |
| POST | `/crisis/alert` | `{crisis_event_id}` | 202 `CrisisAlertRetryOut` |

`location` is `{latitude, longitude, accuracy_m?, captured_at?}`. Timestamps must include a timezone.
Without `location`, the patient's home location is used (`location.source == "home"`), else `"none"`.
`location.stale` is `true` when the GPS fix was taken more than 5 minutes before the press.

`nearby.facilities` lists up to 5 **verified** facilities with a 24-hour emergency unit, a
haematologist or transfusion, nearest first, within 50 km. If none, the search widens once to
150 km and `widened_search` is `true`. Use `call_number` for the call button and
`directions_url` to open maps.

`add_to_passport: true` needs the resolving user's current health-data consent; it can't be
combined with `false_alarm: true`.

## Errors

| Status | `error.code` | Meaning |
|---|---|---|
| 401 | `not_authenticated`, `token_expired`, `token_invalid`, `account_disabled` | See the auth contract |
| 403 | `role_forbidden` | This account type can't do this (e.g. caregiver) |
| 403 | `crisis_access_denied` | Linked, but not allowed (e.g. family without location sharing) |
| 403 | `consent_required` | `details.next` is `/auth/consent`; ask for health-data consent first |
| 404 | `patient_not_found`, `crisis_not_found`, `facility_not_found` | Missing, or not yours to see |
| 409 | `crisis_closed` | Already resolved or cancelled |
| 409 | `client_event_id_reused` | That id was used for another patient; make a new one |
| 422 | `validation_error` | `details[].loc` names the bad field |
| 503 | `sms_not_configured` | Server SMS isn't set up (deployment problem) |
