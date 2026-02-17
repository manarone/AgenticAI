.PHONY: init-db test run-coordinator run-executor run-admin docker-up docker-down

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

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
