# AgentAI MVP Ops Runbook

## Health Checks
- Coordinator: `GET /healthz`
- Executor: `GET /healthz`
- Admin: `GET /healthz`

## Metrics
- Coordinator/Executor/Admin: `GET /metrics`
- Prometheus: service `prometheus:9090`
- Grafana: service `grafana:3000`

## Common Incidents

### 1) Tasks stuck in `QUEUED`
- Verify Redis availability.
- Check executor pod logs.
- Confirm stream names in config map and env are identical.

### 2) Telegram commands ignored
- Confirm webhook points to coordinator.
- Validate bot token secret.
- Check `/start <invite_code>` was completed.

### 3) Memory not retained
- Ensure `MEMORY_BACKEND=mem0_local` in config map/env.
- Confirm Qdrant is healthy and reachable at `MEM0_QDRANT_HOST:MEM0_QDRANT_PORT`.
- Check coordinator logs for mem0 client errors.

### 4) Approval callbacks fail
- Ensure callback data format `approve:<approval_id>` / `deny:<approval_id>`.
- Confirm identity is mapped in `telegram_identities`.

### 5) High token usage
- Query `/admin/token-usage`.
- Adjust model config from MiniMax default if needed.
- Add stricter prompt limits and summarize old context.

## Safe Restart Procedure
1. Scale coordinator to 0.
2. Ensure running tasks are canceled or completed.
3. Restart executor then coordinator.
4. Verify `/healthz` and `/metrics` for all services.

## Backup Pointers
- Postgres PVC snapshots daily.
- MinIO bucket replication enabled.
- Keep audit logs retained for incident review.
