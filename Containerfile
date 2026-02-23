FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --upgrade pip && \
    python -c "import subprocess, tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call(['pip', 'install', *d.get('project', {}).get('dependencies', [])])"

COPY src /app/src
RUN pip install --no-deps .
COPY alembic.ini /app/
COPY alembic /app/alembic

RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser appuser && \
    chown -R appuser:appgroup /app

EXPOSE 8000

# Docker backend requires mounted docker socket access at runtime.
# Temporary: fallback stays enabled until socket wiring is validated in Coolify.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    BUS_BACKEND=inmemory \
    EXECUTION_RUNTIME_BACKEND=docker \
    EXECUTION_RUNTIME_TIMEOUT_SECONDS=300 \
    EXECUTION_DOCKER_IMAGE=python:3.12-slim \
    EXECUTION_DOCKER_MEMORY_LIMIT=512m \
    EXECUTION_DOCKER_NANO_CPUS=500000000 \
    EXECUTION_DOCKER_ALLOW_FALLBACK=true

USER appuser

CMD ["sh", "-c", "i=0; until alembic upgrade head; do i=$((i+1)); if [ \"$i\" -ge 15 ]; then echo 'alembic upgrade failed after retries'; exit 1; fi; echo \"Retrying alembic upgrade ($i/15)\"; sleep 2; done; exec uvicorn agenticai.main:app --host ${HOST} --port ${PORT} --app-dir /app/src"]
