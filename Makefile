.PHONY: install lint test typecheck check up down logs migrate

install:
	python3 -m pip install -e '.[dev]'

lint:
	ruff check .

test:
	pytest

typecheck:
	mypy src

check: lint typecheck test

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f bot

migrate:
	docker compose run --rm bot alembic upgrade head

