# Auth API contract (Day 4)

For the web and mobile tracks. Base URL: `NEXT_PUBLIC_API_URL` (local: `http://localhost:8000/api/v1`).
Live, always-current schemas: `http://localhost:8000/docs`.

## Flows

**New user**

1. `POST /auth/register` → account created, code texted. Response has `user`, `expires_in`, `resend_after`.
2. `POST /auth/otp/verify` with the code → `TokenOut` (user is now signed in and `phone_verified: true`).
3. `GET /auth/me` → read `consents.missing_required`.
4. `POST /auth/consent` once per missing type, after showing the policy text.

**Returning user**

1. `POST /auth/otp/send` → code texted.
2. `POST /auth/login` with phone + code → `TokenOut`.

**Staying signed in**

- Send `Authorization: Bearer <access_token>` on every request. Access tokens last 30 minutes.
- On `401` with `error.code == "token_expired"`, call `POST /auth/refresh` once, retry the request, and store the **new** refresh token. Each refresh token works exactly once.
- If refresh returns `401`, send the user to login.
- Logout: `POST /auth/logout` with the refresh token, then delete both tokens on the device.
- Mobile: keep tokens in `expo-secure-store`, never AsyncStorage. Web: keep them in the NextAuth session (server side), not localStorage.
- Never refresh twice in parallel with the same token. A reused refresh token ends **all** of that user's sessions (theft protection). Queue concurrent requests behind one refresh.

## Phone numbers

Send what the user typed: `08031234567`, `0803 123 4567`, `+2348031234567` all work. Responses always use E.164 (`+2348031234567`). Numbers outside Nigeria need their `+` country code.

## Endpoints

| Method | Path | Auth | Body | Success |
|---|---|---|---|---|
| POST | `/auth/register` | no | `{phone, full_name, role, email?, preferred_language?}` | 201 `RegisterOut` |
| POST | `/auth/otp/send` | no | `{phone}` | 202 `OtpSentOut` |
| POST | `/auth/otp/verify` | no | `{phone, code}` | 200 `TokenOut` |
| POST | `/auth/login` | no | `{phone, code}` | 200 `TokenOut` |
| POST | `/auth/refresh` | no | `{refresh_token}` | 200 `TokenOut` |
| POST | `/auth/logout` | no | `{refresh_token}` | 200 `{message}` |
| POST | `/auth/consent` | yes | `{consent_type, granted, policy_version?}` | 201 `ConsentOut` |
| GET | `/auth/me` | yes | | 200 `MeOut` |
| PATCH | `/auth/me` | yes | `{full_name?, email?, preferred_language?}` | 200 `MeOut` |

- `role`: `patient`, `guardian`, `caregiver`, `facility_admin`. (`ops` accounts are created by an admin.)
- `preferred_language`: `en`, `ig`, `pcm`.
- `consent_type`: `terms`, `health_data`, `location`, `llm_processing`. Required at onboarding: `terms` for everyone, plus `health_data` for patients and guardians.
- `debug_code` in `RegisterOut` / `OtpSentOut` holds the OTP **only in local development** with mock SMS. It is always `null` on staging and production; never build UI that depends on it.

### Shapes

```jsonc
// TokenOut
{
  "access_token": "eyJ...",
  "refresh_token": "Qm9...",
  "token_type": "bearer",
  "expires_in": 1800,            // seconds
  "refresh_expires_in": 1209600,
  "user": { /* UserOut */ }
}

// UserOut
{
  "id": "uuid", "phone": "+2348031234567", "email": null, "full_name": "Ada Obi",
  "role": "guardian", "preferred_language": "en", "phone_verified": true,
  "is_active": true, "created_at": "2026-10-13T14:54:06Z"
}

// MeOut = UserOut + consents
"consents": {
  "terms": true, "health_data": false, "location": false, "llm_processing": false,
  "policy_version": "2026-10-v1",
  "missing_required": ["health_data"]
}
```

## Errors

Every error has the same shape:

```json
{"error": {"code": "otp_invalid", "message": "The code is incorrect.", "details": {"attempts_remaining": 3}}}
```

Show `message` to users; branch on `code`. `429` responses include a `Retry-After` header and `details.retry_after_seconds`.

| Code | Status | Meaning / what to do |
|---|---|---|
| `validation_error` | 422 | Bad body; `details` lists fields |
| `phone_taken` | 409 | Number already has a verified account → go to login |
| `account_not_found` | 404 | No account → go to register |
| `account_disabled` | 403 / 401 | Account switched off by ops |
| `phone_not_verified` | 403 | Login before verifying → use `/auth/otp/verify` |
| `otp_invalid` | 400 | Wrong code; `details.attempts_remaining` |
| `otp_expired` | 400 | Code expired/used → request a new one |
| `otp_locked` | 429 | Too many wrong codes → request a new one |
| `otp_cooldown` | 429 | Resend too soon → countdown from `Retry-After` |
| `otp_rate_limited` | 429 | Hourly limit for number or network |
| `sms_failed` | 503 | SMS provider failed → let the user retry |
| `not_authenticated` | 401 | No token sent |
| `token_expired` | 401 | Call `/auth/refresh` |
| `token_invalid` | 401 | Log in again |
| `refresh_invalid` / `refresh_expired` / `refresh_reused` | 401 | Log in again |
| `policy_outdated` | 409 | Re-fetch the policy and ask again; `details.current_version` |
| `role_forbidden` | 403 | Wrong account type for this endpoint |
| `consent_required` | 403 | Collect `details.consent_type` via `/auth/consent`, then retry |

## For backend developers (Day 5 onward)

```python
from app.core.permissions import UserRole, require_roles
from app.domains.auth.dependencies import CurrentUser, require_consent
from app.domains.auth.models import ConsentType

@router.post("/patients", dependencies=[require_consent(ConsentType.HEALTH_DATA)])
async def create_patient(body: PatientIn, user: CurrentUser, db: DbSession): ...

@router.get("/ops/dashboard", dependencies=[require_roles(UserRole.OPS)])
async def dashboard(): ...
```

- Raise `app.core.exceptions` errors (`NotFoundError`, `ForbiddenError`, …) with a stable `code`.
- Record sensitive actions with `app.core.audit.write_audit(...)` before `await db.commit()`.
- Day 5: add the foreign key `consent_records.patient_id → patients.id`, and replace the
  `patient_consent_unavailable` guard in `auth/service.py::record_consent` with a check
  that the guardian manages that patient.
