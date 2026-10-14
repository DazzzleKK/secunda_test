UV ?= uv

.PHONY: install format lint typecheck imports test check

install:
	$(UV) sync

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy src

imports:
	$(UV) run lint-imports

test:
	$(UV) run pytest -q

check: lint typecheck imports test
