migrate:
	python -m alembic upgrade head

seed:
	python alembic/seeds/seed_districts.py

test:
	pytest
