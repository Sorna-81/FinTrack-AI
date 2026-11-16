# FinTrack AI

An AI powered personal finance and expense intelligence application. The repository contains a React + TypeScript dashboard and a FastAPI service with SQLAlchemy persistence, JWT authentication, per-user data access, transaction CRUD, budgets, savings goals, analytics, categorization, forecasts, and CSV export.

## Project structure

```text
backend/app/       FastAPI, database models, auth, analytics and ML
frontend/src/      React dashboard, forms and charts
database/          PostgreSQL schema reference
documentation/     API and architecture notes
tests/             Backend API tests
```

## Run locally

Requirements: Python 3.10+, Node.js 20+, npm. SQLite is the default for a quick local run. PostgreSQL can be enabled by setting `DATABASE_URL`.

1. Backend setup, from the repository root:

   ```powershell
   cd backend
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   py -m alembic -c alembic.ini upgrade head
   uvicorn app.main:app --reload
   ```

   API docs: http://localhost:8000/docs

2. Frontend, in another terminal:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

   Open http://localhost:5173 and register an account.

## PostgreSQL

Create a database, then set `DATABASE_URL` in `backend/.env` to `postgresql+psycopg://username:password@localhost:5432/fintrack`. Run `py -m alembic -c alembic.ini upgrade head` from the backend folder after setting the URL. The initial migration adopts pre-migration schemas without replacing existing tables and creates missing tables. Never commit `.env` or live credentials.

## Deployment configuration

Set `APP_ENV=production`, a cryptographically random `SECRET_KEY` of at least 32 characters, an HTTPS `FRONTEND_ORIGIN`, `DATABASE_URL=postgresql+psycopg://...`, and `RATE_LIMIT_STORAGE_URI=rediss://...` in the backend environment. Startup validation rejects development/example keys, non-HTTPS origins, SQLite URLs, and non-Redis rate-limit storage in production. Refresh credentials use an HttpOnly cookie; the default `AUTH_COOKIE_SAMESITE=lax` suits a same-site frontend/API deployment. If frontend and API are on different sites, set `AUTH_COOKIE_SAMESITE=none` and use HTTPS for both. Keep `.env` files out of source control. Set frontend `VITE_API_URL` at build time to `/api` when a reverse proxy serves the API on the same origin, or to `https://your-api-host/api` for a separate API origin (and set backend `FRONTEND_ORIGIN` to the exact deployed frontend origin).

Use `/api/health/live` for process liveness and `/api/health/ready` for database, migration-version, and configured Redis readiness. `/metrics` exposes Prometheus request metrics; restrict it to a trusted monitoring network at the reverse proxy or firewall. When deploying behind a proxy, configure Uvicorn's trusted proxy IPs so client-IP rate limits use verified forwarding headers.

### Backup and restore

For PostgreSQL, take encrypted, access-controlled backups with `pg_dump --format=custom --no-owner --file=fintrack-backup.dump $env:FINTRACK_DATABASE_URL`; store the connection URL in a secret manager rather than shell history or source control. Restore into a separate database with `pg_restore --clean --if-exists --no-owner --dbname=$env:FINTRACK_RESTORE_URL fintrack-backup.dump` and validate before switching traffic. Schedule backups, keep off-site retention, document RPO/RTO, and run restore drills. Back up before migrations. For SQLite development data, use its online backup API or `.backup` command rather than copying a live database file.

The application still needs deployment-specific TLS termination, backup restore drills, monitoring collection, and operational validation. Synthetic demo records are intentionally not created in new accounts; dashboard values remain database-driven.

## Features

- Registration, login, bcrypt password hashing, 15-minute access JWTs, rotating database-backed refresh sessions in HttpOnly cookies, revocable logout, protected API routes, and ownership-scoped queries.
- Shared Redis-backed API rate limits in production, liveness/readiness checks, request IDs, request/error logs, and Prometheus metrics.
- Income and expense CRUD, category and date/type/search filtering, CSV export.
- User dashboard values and charts computed from persisted transactions.
- Monthly and category budgets with utilization alerts; savings goals.
- Description classification using a per-user scikit-learn model when enough labeled examples exist, otherwise transparent keyword/previous-category fallback and manual category selection.
- Expense forecast uses a linear trend over monthly totals and clearly reports insufficient history until at least three months exist.
- Insights compare observed category totals and adjacent recorded months. No fabricated metrics are presented.

## API overview

Protected endpoints require `Authorization: Bearer <access-token>`. Registration/login issue short-lived access tokens and an HttpOnly refresh cookie; refresh and logout use that cookie.

| Area | Routes |
|---|---|
| Auth | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET/PATCH /api/auth/me` |
| Transactions | `GET/POST /api/transactions`, `PUT/DELETE /api/transactions/{id}` |
| Categorization | `POST /api/categorize` |
| Analytics | `GET /api/analytics/dashboard`, `/insights`, `/prediction` |
| Categories | `GET /api/categories` |
| Budgets | `GET/POST /api/budgets`, `PUT/DELETE /api/budgets/{id}` |
| Savings goals | `GET/POST /api/goals`, `DELETE /api/goals/{id}` |
| Reports | `GET /api/reports/export.csv` |

Interactive OpenAPI docs are served at `/docs`.

## Notes

This is a portfolio-ready development baseline, not a financial institution product. Before production, configure a strong secret and HTTPS, add Alembic migrations, rate limits, refresh/revocation strategy, audit logging, backups, monitoring, and appropriate deployment security. Synthetic demo records are intentionally not created in a new user's account; dashboard values remain database-driven.
