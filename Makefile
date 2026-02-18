.PHONY: \
	init-db test run-coordinator run-executor run-admin \
	k8s-apply-dev k8s-delete-dev k8s-apply-prod \
	k8s-logs-coordinator k8s-logs-executor k8s-logs-admin \
	k8s-port-forward-coordinator k8s-port-forward-executor k8s-port-forward-admin \
	k8s-port-forward-traefik image-build image-push

NAMESPACE ?= agentai

init-db:
	python3 scripts/init_db.py

test:
	pytest -q

run-coordinator:
	uvicorn services.coordinator.main:app --host 0.0.0.0 --port 8000 --reload

run-executor:
	uvicorn services.executor.main:app --host 0.0.0.0 --port 8001 --reload

run-admin:
	uvicorn services.admin.main:app --host 0.0.0.0 --port 8002 --reload

k8s-apply-dev:
	kubectl apply -k kubernetes-stack/overlays/dev

k8s-delete-dev:
	kubectl delete -k kubernetes-stack/overlays/dev --ignore-not-found

k8s-apply-prod:
	kubectl apply -k kubernetes-stack/overlays/prod

k8s-logs-coordinator:
	kubectl -n $(NAMESPACE) logs -f deploy/coordinator

k8s-logs-executor:
	kubectl -n $(NAMESPACE) logs -f deploy/executor

k8s-logs-admin:
	kubectl -n $(NAMESPACE) logs -f deploy/admin

k8s-port-forward-coordinator:
	kubectl -n $(NAMESPACE) port-forward svc/coordinator 8000:8000

k8s-port-forward-executor:
	kubectl -n $(NAMESPACE) port-forward svc/executor 8001:8001

k8s-port-forward-admin:
	kubectl -n $(NAMESPACE) port-forward svc/admin 8002:8002

k8s-port-forward-traefik:
	kubectl -n kube-system port-forward svc/traefik 8080:80

image-build:
	bash scripts/build_image.sh

image-push:
	bash scripts/build_image.sh --push
