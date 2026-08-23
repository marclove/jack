.PHONY: test lint format typecheck ci

test:
	poetry run pytest

lint:
	poetry run ruff check .
	poetry run ruff format --check .

format:
	poetry run ruff format .

typecheck:
	poetry run ty check

ci: lint typecheck test
