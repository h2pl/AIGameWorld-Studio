# AIGameWorld-Studio development commands.
# Usage: make <target>. `make dev` starts the Studio web server.

.PHONY: help dev serve kb migrate lint test

# Default target: show help
help:
	@echo "AIGameWorld-Studio commands:"
	@echo "  make dev       Start Studio web server (FastAPI + KB UI + crawler UI)"
	@echo "  make serve     Alias of make dev"
	@echo "  make kb        Knowledge base CLI (aw-studio kb)"
	@echo "  make migrate   Init / migrate DB schema"
	@echo "  make lint      ruff check + format check"
	@echo "  make test      Run unit tests"

# Start Studio web server (default port 8888)
dev:
	uv run python -m src.serve --host 127.0.0.1 --port 8888

serve: dev

# Knowledge base CLI
kb:
	uv run python -m scripts.kb

# DB migration (idempotent; also runs automatically on startup)
migrate:
	uv run python -c "from src.utils.sqlite_store import SQLiteStore; from pathlib import Path; SQLiteStore(Path('data/studio.db')).init_schema(Path('migrations'))"

# Lint
lint:
	uv run ruff check src/ scripts/ tests/
	uv run ruff format --check src/ scripts/ tests/

# Unit tests
test:
	uv run python -m pytest tests/ -q
