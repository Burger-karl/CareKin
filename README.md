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

### Backend tests

```bash
docker compose up -d db redis        # Postgres + Redis; creates carekin_test on first run
cd backend && pip install -e ".[dev]" && pytest
```

## Ground rules

- No medical advice, dosing or diagnosis from any AI component — hard line.
- Never hold customer funds directly; licensed escrow partner only.
- Every money movement and sensitive action goes to the audit log.
- Automated tests for every money-moving code path.
- One ADR per major decision (`docs/adr/`).
