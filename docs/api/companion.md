# Care companion API contract (Day 15)

For the web and mobile tracks. Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
Live, always-current schemas: `http://localhost:8000/docs`.

The care companion sends **reminders and daily check-ins** (water, medicine, appointments)
over WhatsApp or SMS, and chats with patients and families in the app or on WhatsApp.
**Reminders only: it never gives medical advice.** An emergency or a medical question is
handed to a person instead, with the Crisis button and the emergency number.

## What to build

| Track | Build |
|---|---|
| Web (ops console) | Handoff view: from an incident whose description says "AI agent handoff", open `GET /agents/companion/history/{session_id}` and show the log with each message's `guardrail_flag`. |
| Web and mobile (patient) | A reminders screen (`/patients/{id}/reminders`) and, if wanted, a companion chat (`POST /agents/companion/message`). |
| Mobile | **Deep link from WhatsApp into the crisis screen**: WhatsApp replies carry `CRISIS_LINK_URL` (an https link, e.g. `https://app.carekin.ng/crisis`). Claim it as an Android App Link / iOS universal link and open `crisis.tsx`; phones without the app get the web crisis page. |

## It's slow: plan the UI for it

Same as the matching agent: the AI runs on our own server, on CPU. A chat message takes
**about 20–45 seconds**. Show a "thinking…" state, keep the message on screen, set the client
timeout to **at least 2 minutes**, and don't retry automatically. The reminder routes are fast.

## Who can use it

| | Companion chat | Reminders | History |
|---|---|---|---|
| Patient (P) | yes | yes, for themselves | own conversations |
| Guardian (G) | yes | yes, for their patients | own conversations |
| Ops (O) | no | no | conversations handed to a person |
| Family abroad, Caregiver, Facility | no (403 `role_forbidden`) | no | no |

Chat needs **`llm_processing` consent**: without it, 403 `consent_required`; send the user to
`POST /auth/consent` with `consent_type: "llm_processing"`. Reminders don't need it.

## Endpoints

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/agents/companion/message` | `CompanionMessageRequest` | 200 `CompanionMessageOut` |
| GET | `/agents/companion/history/{session_id}` | | 200 `ConversationHistoryOut` |
| GET | `/patients/{patient_id}/reminders` | | 200 `ReminderRead[]` |
| POST | `/patients/{patient_id}/reminders` | `ReminderCreate` | 201 `ReminderRead` |
| PATCH | `/patients/{patient_id}/reminders/{reminder_id}` | `ReminderUpdate` | 200 `ReminderRead` |
| GET | `/webhooks/whatsapp` | | Meta only: subscription handshake |
| POST | `/webhooks/whatsapp` | | Meta only: inbound messages |

### Companion chat

`CompanionMessageRequest`:
```json
{ "message": "Remind me to drink water at 10am", "session_id": null, "patient_id": null, "channel": "mobile" }
```
- `message`: 1–1000 characters. `channel`: `web` or `mobile`.
- `patient_id`: who reminders are for. Leave it out and it defaults to the patient's own
  record, or a guardian's only patient. A guardian of several patients should send it; without
  a patient the companion chats but can't set reminders.
- `session_id`: continue a conversation; omit to start one.

`CompanionMessageOut`:
```json
{
  "session_id": "9b1c…",
  "status": "answered",
  "reply": "Done: I'll remind you about water at 10:00 every day.",
  "reply_source": "fallback",
  "reminder": { "id": "…", "kind": "hydration", "local_time": "10:00:00", "days": ["mon", "…", "sun"], "...": "..." }
}
```
- `status`: `answered`, or `handed_off`: show `reply` (our fixed message) and the **Crisis
  button** prominently. That conversation is with a person now (409 `conversation_handed_off`
  if continued); start a new one to talk to the companion again.
- `reply_source`: `model` (written by the AI), `fallback` (written by our code: confirming a
  reminder that was set, listing reminders, or a fixed answer when the AI is down or
  off-topic), or `handoff`.
- `reminder`: the reminder set by this message, if one was: refresh the reminders screen.
- Typing `SOS`, `crisis` or `emergency` alone hands off as an emergency; `help` shows a menu
  that starts with the emergency number.

### Reminders

`ReminderCreate`:
```json
{ "kind": "medication", "local_time": "20:00", "days": ["mon", "wed", "fri"], "on_date": null, "channel": "whatsapp" }
```
- `kind`: `hydration`, `medication`, `appointment` or `check_in` (a daily "how is … today?").
- `local_time`: Lagos time, to the minute.
- `days`: repeats on these days (`mon`…`sun`); leave it out for every day. **Or** `on_date`
  (`YYYY-MM-DD`) for a one-off such as an appointment. Not both.
- `channel`: `whatsapp` (default) or `sms`.

`ReminderRead` adds `id`, `patient_id`, `active`, `next_due_at` and `last_sent_at` (UTC), and
`text`: exactly what will be sent. **The wording is fixed per kind**; medicine reminders never
name a medicine or a dose. A reminder goes to the person who set it: each guardian sees and
manages only their own. `ReminderUpdate` takes any of `active` (false switches it off),
`local_time`, `days`, `on_date`, `channel`.

Limits: 10 active reminders per person per patient. A reminder that can't be sent within an
hour of its time (worker down) is skipped until its next time. Replying **STOP** on WhatsApp
switches off that person's WhatsApp reminders.

### History

`ConversationHistoryOut`: `session_id`, `agent` (`companion` or `matching`), `channel`,
`status`, `patient_id`, `started_at`, `handed_off_at`, `handoff_incident_id`, `view`, and
`messages` (oldest first: `position`, `role`, `content`, `tool_name`, `guardrail_flag`,
`created_at`).
- `view: "owner"`: the user's own conversation, as they saw it: their messages (`user`) and the
  replies (`assistant` from the AI, `system` from our code).
- `view: "ops"`: an ops user reading a conversation that was handed to a person (the incident
  says which). The full log: also `tool` rows (JSON of each API call) and model replies that
  were withheld (`assistant` with `guardrail_flag: "clinical"`). Ops can't read conversations
  that were never handed off (404).
- Every read is written to the audit log.

## WhatsApp setup (ops and backend)

1. In the Meta app dashboard, set the webhook URL to `https://<api-host>/api/v1/webhooks/whatsapp`
   with `WHATSAPP_VERIFY_TOKEN`, and subscribe to `messages`. Set `WHATSAPP_ACCESS_TOKEN`,
   `WHATSAPP_PHONE_NUMBER_ID` and `WHATSAPP_APP_SECRET` (it signs every webhook call; unsigned
   calls get 403).
2. Get these message templates approved (one body parameter, `{{1}}` = the patient's first name),
   with the same wording as `REMINDER_TEXTS` in `backend/app/agents/reminders.py` plus
   "Reply STOP to turn off reminders": `carekin_reminder_hydration`, `carekin_reminder_medication`,
   `carekin_reminder_appointment`, `carekin_reminder_check_in`. Free text only reaches people
   who messaged us in the last 24 hours, so reminders always use templates.
3. Set `CRISIS_LINK_URL` to the https crisis link the mobile app claims.
4. Run Celery Beat: it sends due reminders every minute (`agents.send_reminders`).

On WhatsApp, the sender is matched to an account by phone number. Unknown numbers and other
roles get a fixed reply with the emergency number, and nothing is stored. Without
`llm_processing` consent only emergencies and medical questions are taken (they go to a
person, not the AI); other messages get a reply asking for consent in the app.

## Errors

| Status | `code` | Meaning |
|---|---|---|
| 400 | `reminder_in_past` | That date and time has already passed |
| 401 | `not_authenticated` | No or bad token |
| 403 | `consent_required` | No `llm_processing` consent (chat only) |
| 403 | `role_forbidden` | This role can't use this endpoint |
| 403 | `patient_forbidden` | Linked to this patient only as family |
| 403 | `invalid_signature`, `webhook_verification_failed` | Webhook calls not from Meta |
| 404 | `patient_not_found` | No such patient, or no access to them |
| 404 | `conversation_not_found` | `session_id` isn't yours (or, for ops, wasn't handed off) |
| 404 | `reminder_not_found` | Not one of your reminders for this patient |
| 409 | `conversation_handed_off` | That conversation is with a person now; start a new one |
| 409 | `conversation_closed` | That conversation has ended; start a new one |
| 409 | `conversation_patient_mismatch` | `session_id` belongs to another patient |
| 409 | `too_many_reminders` | 10 active reminders already; switch one off first |
| 422 | | Invalid body (e.g. both `days` and `on_date`) |
