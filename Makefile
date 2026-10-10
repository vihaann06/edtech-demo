# AI explainer-video demo: common dev tasks.  Run `make help`.

SHELL          := /bin/bash
BACKEND_DIR    := backend
FRONTEND_DIR   := frontend
VENV           := $(BACKEND_DIR)/.venv
PYTHON         ?= $(shell command -v python3.12 2>/dev/null || command -v python3)
# Same default as backend/app/config.py; a RENDERER_IMAGE line in .env (or the environment) wins.
ENV_RENDERER_IMAGE := $(shell sed -n 's/^[[:space:]]*RENDERER_IMAGE=[[:space:]]*//p' .env 2>/dev/null | tail -n1 | tr -d "\"'\r ")
RENDERER_IMAGE ?= $(or $(ENV_RENDERER_IMAGE),explainer-manim-renderer:0.19.2)
BACKEND_PORT   ?= 8000
FRONTEND_PORT  ?= 3000

.DEFAULT_GOAL := help
.PHONY: help setup setup-backend setup-frontend renderer dev backend frontend test check

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-16s %s\n", $$1, $$2}'

setup: setup-backend setup-frontend renderer ## Install everything: backend venv, frontend deps, renderer image

setup-backend: ## Create backend/.venv and install Python deps
	@test -x $(VENV)/bin/python || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -r $(BACKEND_DIR)/requirements.txt

setup-frontend: ## npm install in frontend/
	cd $(FRONTEND_DIR) && npm install

renderer: ## Build the Manim renderer Docker image (needs Docker running)
	docker build -f docker/Dockerfile.renderer -t $(RENDERER_IMAGE) docker/

dev: ## Start backend (:8000) and frontend (:3000) together; Ctrl-C stops both (RELOAD=1 for backend autoreload)
	@BACKEND_PORT=$(BACKEND_PORT) FRONTEND_PORT=$(FRONTEND_PORT) bash scripts/dev.sh

backend: ## Start only the backend on :8000 with autoreload
	cd $(BACKEND_DIR) && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $(BACKEND_PORT) --reload --reload-dir app

frontend: ## Start only the frontend on :3000
	cd $(FRONTEND_DIR) && npm run dev -- --port $(FRONTEND_PORT)

test: ## Run the backend test suite
	cd $(BACKEND_DIR) && .venv/bin/pytest -q

check: ## Report which prerequisites are present (venv, node, docker, image, API key)
	@RENDERER_IMAGE=$(RENDERER_IMAGE) bash scripts/check.sh
