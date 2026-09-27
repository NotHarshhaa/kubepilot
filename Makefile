.PHONY: install run test lint docker-build compose-up compose-down

install:
	uv sync --extra dev || pip install -e ".[dev]"

run:
	python -m app.main

test:
	pytest -q

lint:
	ruff check app tests

docker-build:
	docker build -t kubepilot:latest .

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down
