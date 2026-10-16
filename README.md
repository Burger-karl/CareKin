# CareKin

A care marketplace, safety layer and facility directory for people living with sickle cell disease.
HackathonAfrica 3.0 — Team 2.

## Monorepo layout

| Workspace  | Stack                          |
|------------|--------------------------------|
| `backend/` | FastAPI (Python 3.12)          |
| `web/`     | Next.js 14                     |
| `mobile/`  | React Native / Expo            |
| `infra/`   | Terraform, Docker, CI/CD       |
| `docs/`    | ADRs, architecture diagrams    |

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

API: http://localhost:8000/api/v1 · Docs: http://localhost:8000/docs

The backend container applies migrations (`alembic upgrade head`) on start.
Auth API contract for web and mobile: [`docs/api/auth.md`](docs/api/auth.md).
Crisis button contract: [`docs/api/crisis.md`](docs/api/crisis.md).

Compose also starts a Celery `worker` and `beat`. Beat retries crisis SMS that failed
(`crisis.sweep_alerts`, every 30 s). Crisis alerts are first sent by the API itself, so
the button keeps working if Redis or Celery is down. Locally, SMS is mocked
(`USE_MOCK_INTEGRATIONS=true`); production refuses a mocked SMS provider.

### Backend tests

```bash
docker compose up -d db redis        # Postgres + Redis; creates carekin_test on first run
cd backend && pip install -e ".[dev]" && pytest
```

### Demo data

```bash
cd backend
python -m seed.facilities --demo-verified   # Enugu facilities (unconfirmed; verified for demos only)
python -m seed.crisis_simulator --presses 5 # presses the crisis button end to end, never sends real SMS
```

Seeds refuse to run in production. Partners (NIN, background checks, escrow, payments,
WhatsApp, S3) run on deterministic mocks while `USE_MOCK_INTEGRATIONS=true`; see
[ADR 0004](docs/adr/0004-integrations-behind-mocks.md) for what each mock does.

## Ground rules

- No medical advice, dosing or diagnosis from any AI component — hard line.
- Never hold customer funds directly; licensed escrow partner only.
- Every money movement and sensitive action goes to the audit log.
- Automated tests for every money-moving code path.
- One ADR per major decision (`docs/adr/`).
