"""Frozen prompts for hosted LLM runs. Changing any text requires bumping PROMPT_VERSION,
which is stored with every cached response."""

PROMPT_VERSION = "v1"

SUMMARIZE = (
    "You maintain an agent's memory. Merge the notes below into one short summary of plain"
    " declarative sentences. Keep every distinct fact, including names, codes and numbers,"
    " exactly as written. Do not add facts. Output only the summary."
)

ANSWER = (
    "Answer the question using only the context. Quote the relevant facts as plain sentences,"
    " keeping names, codes and numbers exactly as written. If the context has nothing"
    " relevant, reply exactly: No relevant information."
)
