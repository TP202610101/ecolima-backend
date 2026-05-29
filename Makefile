migrate:
	python -m alembic upgrade head

seed:
	python alembic/seeds/seed_districts.py
	python alembic/seeds/seed_admin.py

test:
	pytest
