FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/

RUN pip install --upgrade pip && \
    python -c "import subprocess, tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); subprocess.check_call(['pip', 'install', *d.get('project', {}).get('dependencies', [])])"

COPY src /app/src
RUN pip install --no-deps .

RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser appuser && \
    chown -R appuser:appgroup /app

EXPOSE 8000

ENV HOST=0.0.0.0 \
    PORT=8000 \
    BUS_BACKEND=inmemory

USER appuser

CMD ["sh", "-c", "exec uvicorn agenticai.main:app --host ${HOST} --port ${PORT} --app-dir /app/src"]
