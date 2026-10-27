default:
    @just --list

sync:
    uv sync --locked

check:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    uv run pytest
