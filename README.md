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

## Local Run (Python Processes)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# set OPENAI_API_KEY and TELEGRAM_BOT_TOKEN in .env
# optional: set APP_TIMEZONE (default UTC) and PROMPT_DIR if prompts are outside src/prompts
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

## Tests
```bash
pytest -q
```

## Time-Sensitive Web Answers
- Hybrid routing is enabled:
- Time-sensitive natural language requests (for example weather/news with `today`, `latest`, `current`) are forced to deterministic `WEB` handling.
- Non-time-sensitive open-ended chat still uses LLM tool mode.
- Deterministic web replies now include:
- `As of` timestamp (UTC + configured local timezone)
- freshness warning when publication dates are missing/unclear
- dated source links (`date: YYYY-MM-DD` or `date: unknown`)

## Kubernetes (K3s-Only)
Create and apply secrets:
```bash
kubectl -n agentai apply -f kubernetes-stack/base/secret.example.yaml
```

Deploy dev overlay:
```bash
kubectl apply -k kubernetes-stack/overlays/dev
```

Follow logs:
```bash
kubectl -n agentai logs -f deploy/coordinator
kubectl -n agentai logs -f deploy/executor
kubectl -n agentai logs -f deploy/admin
kubectl -n agentai logs -f deploy/telegram-poller
```

Ingress + local domains (dev):
```bash
echo "127.0.0.1 coordinator.agentai.local admin.agentai.local" | sudo tee -a /etc/hosts
kubectl -n kube-system port-forward svc/traefik 8080:80
```
Then use:
- `http://coordinator.agentai.local:8080/healthz`
- `http://admin.agentai.local:8080/healthz`

Fallback service access:
```bash
kubectl -n agentai port-forward svc/coordinator 8000:8000
kubectl -n agentai port-forward svc/admin 8002:8002
```

Delete dev overlay:
```bash
kubectl delete -k kubernetes-stack/overlays/dev --ignore-not-found
```

## Image Build/Push (Podman/Buildah)
```bash
bash scripts/build_image.sh
bash scripts/build_image.sh --tag latest --push
```

Set optional env for custom registry/name:
- `REGISTRY` (default `ghcr.io`)
- `IMAGE_NAME` (default `$USER/agentai`)
- `TAG` (default `local`)

## CI/CD
- `.github/workflows/ci.yml`: syntax + tiered pytest gate (`mvp_smoke` or `safety_critical`) + kustomize render checks on push/PR.
- `.github/workflows/ci-full.yml`: full `pytest -q` gate on `main`, nightly schedule, and manual dispatch.
- `.github/workflows/image.yml`: Buildah image build and GHCR push on `main`/tags.
