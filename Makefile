.PHONY: test experiments

test:
	uv run python -m pytest

experiments:
	uv run python -m experiments.run
