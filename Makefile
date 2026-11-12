.PHONY: test demo experiments experiments-groq experiments-groq-replay

# Each recipe names the extras it needs, so it also works from a clean clone.
test:
	uv run --extra testbed --extra llm --extra dashboard python -m pytest

demo:
	uv run --extra dashboard python -m retract.cli demo

experiments:
	uv run --extra testbed python -m experiments.run

experiments-groq:
	uv run --extra testbed --extra llm python -m experiments.run --llm groq

experiments-groq-replay:
	uv run --extra testbed --extra llm python -m experiments.run --llm groq --replay
