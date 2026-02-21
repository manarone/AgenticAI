FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

ENV HOST=0.0.0.0 \
    PORT=8000 \
    BUS_BACKEND=inmemory

CMD ["uvicorn", "agenticai.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/src"]
