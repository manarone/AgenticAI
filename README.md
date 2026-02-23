![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/manarone/AgenticAI?utm_source=oss&utm_medium=github&utm_campaign=manarone%2FAgenticAI&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

# AgenticAI

Secure, enterprise-first agent platform scaffold.

## Tech stack

- Python 3.12
- FastAPI
- Uvicorn
- Pytest + Ruff
- Containerized with `Containerfile` (Coolify-ready)

## Project layout

```text
src/agenticai/
  api/
    routes/
  bus/
  core/
  db/
alembic/
  versions/
alembic.ini
tests/
Containerfile
pyproject.toml
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn agenticai.main:app --app-dir src --reload --host 127.0.0.1 --port 8000
```

Run checks:

```bash
ruff check .
pytest
```

## Database and migrations

Track A foundation schema is managed with Alembic.

```bash
# Apply baseline schema
alembic upgrade head

# Roll back one revision
alembic downgrade -1
```

Latest migration sets `runtime_settings.bus.redis_fallback_to_inmemory=false` so Redis startup fallback is opt-in via persistent config/env overrides.

## API status

Current scaffold endpoints:

- `GET /healthz`
- `GET /readyz`
- `POST /telegram/webhook`
- `GET /v1/tasks`
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/tasks/{task_id}/cancel`

`/v1/tasks*` requires:

- `Authorization: Bearer <TASK_API_AUTH_TOKEN>`
- `X-Actor-User-Id: <user_uuid>`
- `X-Actor-Signature: sha256=<hmac_hex>` when `TASK_API_ACTOR_HMAC_SECRET` is configured
  HMAC input is the canonical lowercase `X-Actor-User-Id` UUID string (UTF-8 bytes),
  signed with `TASK_API_ACTOR_HMAC_SECRET` using HMAC-SHA256 and lowercase hex output.

Task creation supports idempotent retries with optional `Idempotency-Key` header.

## Deployment notes

- Container entrypoint serves on port `8000`
- Container startup runs `alembic upgrade head` before launching Uvicorn
- Supported queue backends: `inmemory` (default) and `redis`
- Set `BUS_BACKEND=redis` and `REDIS_URL=redis://host:6379/0` to enable Redis queues
- `BUS_REDIS_FALLBACK_TO_INMEMORY` controls whether startup falls back to `inmemory` if Redis is unavailable (default `false`)
- Coordinator worker runs in-process and consumes the `tasks` queue in the background
- Optional coordinator tuning:
  - `COORDINATOR_POLL_INTERVAL_SECONDS` (default `0.1`)
  - `COORDINATOR_BATCH_SIZE` (default `10`)
- Set `TASK_API_AUTH_TOKEN` for `/v1/tasks*` authentication
- Required: set `TASK_API_ACTOR_HMAC_SECRET` outside local/dev/test when `TASK_API_AUTH_TOKEN` is used (startup fails if missing)
- Set `TELEGRAM_WEBHOOK_SECRET` and configure Telegram webhook secret token header to match
- Optional hardening overrides:
  - `ALLOW_INSECURE_TASK_API=true` (dev/local only)
  - `ALLOW_INSECURE_TELEGRAM_WEBHOOK=true` (dev/local only)
- `DATABASE_URL` must not use SQLite outside local/dev/test
- `/docs`, `/redoc`, and OpenAPI are disabled outside local/dev/test
- Health check path: `/healthz`
- Run `alembic upgrade head` against the target database before restarting or rolling out.

### Coolify + GitHub Actions

`CI` now triggers deployment to Coolify after tests pass on `main`.

Required GitHub repository secrets (for deploy verification):

- `COOLIFY_WEBHOOK`: Coolify deploy webhook URL for `agenticai-dev`
- `COOLIFY_TOKEN`: Coolify API token used in the `Authorization: Bearer` header

Repository variables:

- `COOLIFY_APP_UUID`: Coolify app UUID (`kckwwog8owcw4ss0cwwkokcw`)
- `COOLIFY_DEPLOY_REQUIRED`: set to `true` to fail CI when deploy config is missing

Recommended:

- Keep `BUS_BACKEND=inmemory` in Coolify env vars unless Redis is configured.
- If you deploy via this workflow, disable overlapping auto-deploy triggers in Coolify to avoid double deployments.
- The deploy job now validates the real Coolify deployment result (not just webhook success) by polling deployments for the current commit.
- The container image includes `curl` so Coolify health checks can run for Dockerfile-based deploys.
- If deploy credentials are missing and `COOLIFY_DEPLOY_REQUIRED` is not `true`, the deploy job warns and skips verification.
