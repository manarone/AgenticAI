# AgentAI Bootstrap (MVP)

## Prerequisites
- K3s cluster reachable over Tailnet.
- `agentai` namespace deployed from `kubernetes-stack/overlays/dev` or `prod`.
- Telegram bot token from BotFather.
- OpenRouter API key (or any OpenAI-compatible key).
- Qdrant service available in cluster (provided in this stack).

## 1) Configure Secrets
Create `agentai-secrets` from `kubernetes-stack/base/secret.example.yaml` and set:
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `ADMIN_TOKEN`
- `DATABASE_URL`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`

Optional (only for cloud mem0 mode): `MEM0_API_KEY`

Example:
```bash
kubectl -n agentai apply -f kubernetes-stack/base/secret.example.yaml
```

## 2) Deploy
```bash
kubectl apply -k kubernetes-stack/overlays/dev
```

Note: coordinator state persistence uses `coordinator-data-pvc` with `ReadWriteOnce` in MVP, so run coordinator as a single replica unless you switch to `ReadWriteMany` storage.

## 3) Initialize Database
Run once from any app pod:
```bash
python scripts/init_db.py
```

## 4) Create Invite Code
```bash
curl -X POST http://admin.agentai.internal/admin/invite-codes \
  -H "X-Admin-Token: <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"ttl_hours":24}'
```

## 5) User Onboarding
From Telegram:
```text
/start <invite_code>
```

## 6) Test Core Commands
- `/status`
- `/cancel all`
- `shell: echo hello`
- `skill: timesheet this week`

## 7) Configure Webhook
Point Telegram webhook to:
`POST /telegram/webhook` on coordinator service.

Example:
```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<public-coordinator-host>/telegram/webhook"
```
