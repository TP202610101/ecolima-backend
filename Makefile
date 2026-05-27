migrate:
	alembic upgrade head

seed:
	python -m alembic.runtime.migration

test:
	pytest
