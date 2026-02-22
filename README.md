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
uvicorn agenticai.main:app --app-dir src --reload --host 0.0.0.0 --port 8000
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

## API status

Current scaffold endpoints:

- `GET /healthz`
- `GET /readyz`
- `POST /telegram/webhook`
- `GET /v1/tasks`
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/tasks/{task_id}/cancel`

## Deployment notes

- Container entrypoint serves on port `8000`
- Supported queue backends: `inmemory` (default) and `redis`
- Set `BUS_BACKEND=redis` and `REDIS_URL=redis://host:6379/0` to enable Redis queues
- Set `TELEGRAM_WEBHOOK_SECRET` and configure Telegram webhook secret token header to match
- Health check path: `/healthz`
- Run `alembic upgrade head` against the target database before restarting or rolling out.

### Coolify + GitHub Actions

`CI` now triggers deployment to Coolify after tests pass on `main`.

Required GitHub repository secrets:

- `COOLIFY_WEBHOOK`: Coolify deploy webhook URL for `agenticai-dev`
- `COOLIFY_TOKEN`: Coolify API token used in the `Authorization: Bearer` header
- `COOLIFY_APP_UUID`: Coolify app UUID (`kckwwog8owcw4ss0cwwkokcw`) (can be a repository variable instead)

Recommended:

- Keep `BUS_BACKEND=inmemory` in Coolify env vars unless Redis is configured.
- If you deploy via this workflow, disable overlapping auto-deploy triggers in Coolify to avoid double deployments.
- The deploy job now validates the real Coolify deployment result (not just webhook success) by polling deployments for the current commit.
- The container image includes `curl` so Coolify health checks can run for Dockerfile-based deploys.
- If deploy credentials are not configured, the deploy job exits with a warning and skips verification instead of failing unrelated CI checks.
