.PHONY: test experiments experiments-groq experiments-groq-replay

test:
	uv run python -m pytest

experiments:
	uv run python -m experiments.run

experiments-groq:
	uv run python -m experiments.run --llm groq

experiments-groq-replay:
	uv run python -m experiments.run --llm groq --replay
