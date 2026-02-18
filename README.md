# AgentAI MVP

K3s-first AgentAI MVP implementation with:
- Shared Telegram bot + invite-code onboarding
- Coordinator/executor architecture
- Redis Streams task bus
- Postgres tenant-ready schema
- Real mem0 local backend integration (`MEMORY_BACKEND=mem0_local`) with Qdrant
- Skills as Markdown + YAML manifests
- Approval gates for destructive actions
- Minimal admin API/UI
- Prometheus/Grafana manifests

## Services
- `services.coordinator.main:app`
- `services.executor.main:app`
- `services.admin.main:app`

## Local Run (dev)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# set OPENAI_API_KEY and TELEGRAM_BOT_TOKEN in .env
# default mem0 embedder is local fastembed + nomic-ai/nomic-embed-text-v1.5
python3 scripts/init_db.py
uvicorn services.coordinator.main:app --reload --port 8000
uvicorn services.executor.main:app --reload --port 8001
uvicorn services.admin.main:app --reload --port 8002
```

If you do not want to use mem0 while developing/tests:
```bash
export MEMORY_BACKEND=local
```

Cloud mem0 is still supported via `MEMORY_BACKEND=mem0_api` and `MEM0_API_KEY`.

For `MEMORY_BACKEND=mem0_local`, embeddings default to local `fastembed` (`MEM0_EMBEDDER_PROVIDER=fastembed`).
The first request downloads the embedding model weights and can take ~30-120s depending on network/CPU.
Default local stack uses `MEM0_LLM_PROVIDER=lmstudio` to avoid requiring an OpenAI key for mem0 init.
Set `MEM0_LLM_BASE_URL` to your OpenAI-compatible local endpoint (default: `http://localhost:1234/v1`) if you enable mem0 inference/rerank paths.

## Tests
```bash
pytest -q
```

## Kubernetes
```bash
kubectl apply -k kubernetes-stack/overlays/dev
```

## Docker
Build image:
```bash
docker build -t agentai:latest .
```

Run coordinator:
```bash
docker run --rm -p 8000:8000 --env-file .env agentai:latest
```

Run executor/admin by overriding command:
```bash
docker run --rm --env-file .env agentai:latest uvicorn services.executor.main:app --host 0.0.0.0 --port 8001
docker run --rm --env-file .env agentai:latest uvicorn services.admin.main:app --host 0.0.0.0 --port 8002
```

### Docker Compose (recommended local test)
This stack includes `postgres`, `redis`, `minio`, `qdrant`, `coordinator`, `executor`, `admin`, and a `telegram-poller`.
The poller uses Telegram `getUpdates` and forwards updates to coordinator, so no public webhook URL is required for local testing.

```bash
docker compose up --build -d
docker compose logs -f coordinator executor admin telegram-poller
```

Stop:
```bash
docker compose down
```

## CI/CD
- `.github/workflows/ci.yml`: syntax + pytest on push/PR.
- `.github/workflows/docker.yml`: builds image and pushes to GHCR on `main`/tags.
