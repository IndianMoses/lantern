.PHONY: install dev backend frontend build test evals evidence seed docker

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

dev: build backend

seed:
	cd backend && python -m app.seed.seed

test:
	cd backend && python -m pytest tests -q

evals:
	cd backend && python -m evals.run_evals

evidence:
	python scripts/generate_evidence.py

docker:
	docker compose up --build
