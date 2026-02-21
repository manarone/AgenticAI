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
uvicorn agenticai.main:app --app-dir src --reload --host 0.0.0.0 --port 8000
```

Run checks:

```bash
ruff check .
pytest
```

## API status

Current scaffold endpoints:

- `GET /healthz`
- `GET /readyz`
- `GET /v1/tasks`
- `POST /v1/tasks`

## Deployment notes

- Container entrypoint serves on port `8000`
- Set `BUS_BACKEND=inmemory` unless Redis bus is wired
- Health check path: `/healthz`
