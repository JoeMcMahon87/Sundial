# Sundial. Everything here is expected to work from a clean checkout.

.PHONY: help install check backend-check frontend-check infra-check build synth dev-api dev-web clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Set up all three toolchains
	cd backend && uv venv --python 3.13 && uv pip install -e ".[dev]"
	cd infra && uv venv --python 3.13 && uv pip install -e ".[dev]"
	cd frontend && npm ci

check: backend-check frontend-check infra-check ## Everything CI runs

backend-check:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . \
		&& .venv/bin/mypy && .venv/bin/python -m pytest -q

frontend-check:
	cd frontend && npx eslint . && npx tsc -b --noEmit && npm test

infra-check:
	cd infra && .venv/bin/ruff check . && .venv/bin/ruff format --check . \
		&& .venv/bin/mypy app.py sundial_infra tests && .venv/bin/python -m pytest -q

build: ## Build the Lambda asset SundialApp deploys
	cd backend && ./build.sh

synth: build ## Synthesise both stacks for dev
	cd infra && PATH=".venv/bin:$$PATH" npx --yes aws-cdk@2 synth -q

dev-api: ## Backend on :8000 — needs AWS credentials for the dev environment
	cd backend && .venv/bin/uvicorn sundial.api.app:app --reload --port 8000

dev-web: ## Frontend on :5173, proxying /api to :8000
	cd frontend && npm run dev

clean:
	rm -rf backend/dist infra/cdk.out frontend/dist frontend/dev-dist
