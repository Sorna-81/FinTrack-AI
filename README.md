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

## Supabase PostgreSQL

FinTrack keeps FastAPI, SQLAlchemy, Alembic, and its existing JWT/HttpOnly-cookie authentication. Supabase supplies only the managed PostgreSQL database; do not place the database URL, Supabase service role key, or any database password in the frontend.

1. Create a project in the [Supabase Dashboard](https://supabase.com/dashboard), then wait until its database is ready.
2. Open **Connect** (or **Project Settings > Database**) and copy a PostgreSQL connection string. For a deployed API, prefer the Supabase pooler connection; for local migrations, the direct connection is also suitable when your network supports IPv6.
3. From `backend`, copy `.env.example` to the untracked `.env` file and set the values below. URL-encode reserved password characters, or paste the URL provided by Supabase unchanged. Both `postgresql://...` and `postgresql+psycopg://...` are accepted; the backend selects psycopg automatically.

   ```dotenv
   DATABASE_URL=postgresql://postgres.PROJECT_REF:YOUR_URL_ENCODED_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
   DATABASE_SSL_MODE=verify-full
   DATABASE_DISABLE_PREPARED_STATEMENTS=true
   APP_ENV=production
   SECRET_KEY=replace-this-with-the-output-of-the-command-below
   FRONTEND_ORIGIN=https://your-frontend.example
   RATE_LIMIT_STORAGE_URI=rediss://your-production-redis.example:6379/0
   METRICS_TOKEN=replace-this-with-a-separate-random-token
   ```

   Generate `SECRET_KEY` with `py -c "import secrets; print(secrets.token_urlsafe(48))"` and replace the placeholder above. Production validates minimum length and estimated character entropy. Use `DATABASE_SSL_MODE=verify-full` so PostgreSQL TLS verifies the server certificate and hostname; the application and Alembic migrations use this setting. `DATABASE_DISABLE_PREPARED_STATEMENTS=true` is recommended for a transaction-pooler URL. Use `APP_ENV=development` and SQLite for local-only development, or provide a non-production secret and origin for a local Supabase test.
4. Back up any existing cloud database, then run the migration from the `backend` directory:

   ```powershell
   py -m alembic -c alembic.ini current
   py -m alembic -c alembic.ini upgrade head
   py -m alembic -c alembic.ini current
   ```

   Alembic reads `DATABASE_URL` (or an explicit `ALEMBIC_DATABASE_URL`) and records the revision in `alembic_version`. The initial migration creates only missing FinTrack tables and does not drop existing tables; running `upgrade head` again is a no-op after the revision is recorded. Never run `alembic stamp head` on a non-empty database unless its schema has been independently verified as compatible.

5. Start FastAPI normally with `uvicorn app.main:app --reload`. The `/api/health/ready` endpoint confirms connectivity and that the database revision is current. Configure Supabase database backups and network restrictions in the Supabase dashboard; this application does not use Supabase Auth.

## Deployment configuration

Set `APP_ENV=production`, high-entropy `SECRET_KEY` and `METRICS_TOKEN` values generated with `secrets.token_urlsafe(48)`, an HTTPS `FRONTEND_ORIGIN`, `DATABASE_URL=postgresql+psycopg://...`, `DATABASE_SSL_MODE=verify-full`, and `RATE_LIMIT_STORAGE_URI=rediss://...` in the backend environment. Startup validation rejects weak/example keys, non-HTTPS origins, SQLite URLs, unverified PostgreSQL TLS, and non-TLS Redis in production. The `/metrics` endpoint requires `Authorization: Bearer $METRICS_TOKEN` (or `X-Metrics-Token`) and should still be limited to a trusted monitoring network at the proxy. Refresh credentials use an HttpOnly cookie. For separate HTTPS Vercel and Render domains, set `AUTH_COOKIE_SAMESITE=none`; the application automatically sets `Secure` for this mode and uses the same settings when deleting the cookie. Keep `.env` files out of source control. Set frontend `VITE_API_URL` at build time to `/api` when a reverse proxy serves the API on the same origin, or to `https://your-api-host/api` for a separate API origin (and set backend `FRONTEND_ORIGIN` to the exact deployed frontend origin).

### Vercel and Render deployment

1. Import the repository into Vercel with the project root set to `frontend`. The included `frontend/vercel.json` builds `dist` and preserves client-side routes. Set `VITE_API_URL` to the deployed Render API URL ending in `/api`; this is public frontend configuration, not a secret.
2. Create the Render web service from the included `render.yaml`. Set the secret `DATABASE_URL` to the Supabase connection string, `SECRET_KEY`, `FRONTEND_ORIGIN` to the exact Vercel origin, `RATE_LIMIT_STORAGE_URI` to the Upstash `rediss://` URL, and a separate `METRICS_TOKEN`. The template sets `AUTH_COOKIE_SAMESITE=none` for cross-site Vercel/Render authentication. Render runs Alembic `upgrade head` as its release command before starting Uvicorn.
3. In Upstash, enable TLS and copy the Redis TLS URL. In Supabase, use a pooler/direct URL compatible with the deployment network and verify its hostname/certificate before traffic is enabled. Never put either service credential in Vercel variables or the repository.
4. After the first release, check `/api/health/live` and `/api/health/ready`, verify the current Alembic revision, confirm CORS from the Vercel origin, and scrape `/metrics` only with the metrics token from a restricted monitoring network.

The application currently enforces per-user data access in its FastAPI/SQLAlchemy queries; PostgreSQL row-level security is not enabled. Use a dedicated, non-owner database role with only the runtime privileges required, keep migration/owner credentials separate, and never expose either database credential to the frontend. RLS remains deferred until the authenticated tenant identity can be securely integrated with database roles and policies and tested against a staging Supabase project.

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
