# Deployment

## Local development
```bash
make install          # backend deps (pip) and frontend deps (npm)
cp .env.example .env  # set SECRET_KEY; add ANTHROPIC_API_KEY to use the Claude API
make build            # build the SPA into frontend/dist (served by the backend)
make backend          # API + app on http://127.0.0.1:8000, auto seeds an empty database
```
For live reload of the interface run `make frontend` in a second terminal (Vite on :5173 proxies `/api` to :8000).

Reset the demo data at any time: `make seed` (or `POST /api/dev/reset-demo` in development).

## Docker
```bash
docker compose up --build
```
The image builds the SPA, installs the backend and runs uvicorn as a non root user. Data and logs are kept in named volumes. Set `APP_ENV=production`, a real `SECRET_KEY`, `COOKIE_SECURE=true` and `ANTHROPIC_API_KEY` in the environment or a `.env` file next to the compose file. In production the dev router (demo accounts, reset) is not mounted and the database is not auto seeded; run `python -m app.seed.seed` once if you want the demonstration data.

## Postgres
Set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/lantern` and install `psycopg[binary]`. Tables are created on start with `create_all`; for a managed schema generate Alembic migrations from `app/models.py`.

## Behind a reverse proxy
Terminate TLS at the proxy, forward to :8000, keep `COOKIE_SECURE=true`. The app sets a content security policy (`default-src 'self'`, scripts from the app's own origin only, no inline scripts), `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy`, `Permissions-Policy` (camera limited to the app's origin) and `nosniff`; add HSTS at the proxy. The SPA and the API must share an origin, which the CSP and the service worker assume. Uploaded files are never served from a public path; downloads go through authenticated endpoints.

## Analysis models (optional)
Serve the three open-weight models (`docs/MODELS.md`) inside your network with vLLM or SGLang and set `MODEL_A_URL`, `MODEL_B_URL`, `MODEL_C_URL` (and keys if the serving layer requires them). Without them the deterministic analysts run and every job says so. Analyses run in a background thread by default (`ANALYSIS_RUN_IN_BACKGROUND=true`); the interface polls `GET /api/gaps/jobs/{id}`.

## Mobile
The interface is a progressive web app: `manifest.webmanifest`, icons and a service worker that caches the static shell only (never `/api`). Installation requires HTTPS (or localhost). Camera capture for evidence uses the browser's file input with `capture="environment"`, so no native wrapper is needed.

## Operations
* Health: `GET /api/health` (includes an audit chain verification).
* Logs: structured application logs to stdout; operation logs as JSONL under `OPERATION_LOG_DIR` (one file per agent run, masked); audit log in the database (`audit_events`), verify with `GET /api/dev/audit-chain` in development or `audit.verify_chain` from a shell.
* Backups: back up the database and `UPLOAD_DIR` together; the audit chain can be verified after restore.
* Scaling: run several uvicorn workers behind the proxy once the rate limiter and sessions are moved to Redis (single process safe as shipped).

## Production checklist (before any real users)
1. Real `SECRET_KEY`, `APP_ENV=production`, `COOKIE_SECURE=true`, `EXPOSE_DEMO_ACCOUNTS=false`.
2. Remove or re-seed fictional data; never mix it with real policies.
3. `MFA_REQUIRED_FOR_PRIVILEGED=true` once insurer and bank administrators have enrolled (Security page); email delivery for password reset links (the token endpoint exists, delivery does not); review `LOCKOUT_THRESHOLD` and `LOCKOUT_MINUTES`.
4. Real insurer adapters with webhook signature verification; reconciliation job for submissions.
5. Antivirus scanning on uploads; OCR if image uploads must be read.
6. Privacy impact assessment, retention schedule, regulatory perimeter assessment (see the security self assessment).
7. Monitoring and alerting on `submission_failed`, provider fallbacks, analysis jobs in `mixed` mode and audit chain verification.
8. Replace the eight mock bank adapters one interface at a time (`docs/BANK_INTEGRATION.md`); the integration page shows which are still mocks.
9. Raise `K_ANON` in `routers/bank.py` if the bank's customer base makes three too small to be safe.
