"""Append-only JSONL cache of hosted LLM responses, so Groq runs can be replayed without a key.

One entry per distinct request: model id, prompt version, date and response, keyed by a hash of
the request body. Keys and headers are never stored. The cache is read-through, so a live run
also gives identical answers to identical requests, and its replay matches it exactly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retract.core.errors import RetractError
from retract.receipts.receipt import canonical


def request_key(body: dict[str, Any], prompt_version: str) -> str:
    return hashlib.sha256(
        canonical({**body, "prompt_version": prompt_version}).encode()
    ).hexdigest()


class ResponseCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, str] = {}
        self._torn_tail = False
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if not line:
                continue
            try:
                e = json.loads(line)
                self.entries[e["key"]] = e["response"]
            except (ValueError, KeyError, TypeError) as err:
                if i == len(lines) - 1:  # a crash mid-append tears only the last line
                    self._torn_tail = True
                    continue
                raise RetractError(f"{path}:{i + 1}: corrupt cache entry") from err

    def get(self, key: str) -> str | None:
        return self.entries.get(key)

    def put(
        self,
        key: str,
        response: str,
        *,
        model: str,
        prompt_version: str,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._torn_tail:  # drop it, so the file stays valid JSONL
            data = self.path.read_bytes()
            with self.path.open("r+b") as f:
                f.truncate(data.rfind(b"\n") + 1)
            self._torn_tail = False
        entry: dict[str, Any] = {
            "key": key,
            "model": model,
            "prompt_version": prompt_version,
            "date": datetime.now(UTC).date().isoformat(),
            "response": response,
        }
        if usage:
            entry["usage"] = dict(usage)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self.entries[key] = response
