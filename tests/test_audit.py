"""M6: `retract audit` flags likely bypasses and honours the allowlist."""

from pathlib import Path
from textwrap import dedent

import pytest

from retract.audit import audit, audit_source
from retract.cli import main

ROOT = Path(__file__).resolve().parents[1]


def calls(src: str) -> list[tuple[str, str]]:
    return [(f.call, f.client) for f in audit_source(dedent(src).encode(), Path("m.py"))]


def test_mem0_constructor_classmethod_and_module_alias() -> None:
    assert calls(
        """
        import mem0 as m0
        from mem0 import Memory

        a = Memory()
        a.add("x", user_id="u")
        b = Memory.from_config({})
        b.add("y")
        m0.MemoryClient().add("z")
        """
    ) == [
        ("a.add", "mem0.Memory"),
        ("b.add", "mem0.Memory"),
        ("m0.MemoryClient().add", "mem0.MemoryClient"),
    ]


def test_self_attribute_and_import_inside_function() -> None:
    assert calls(
        """
        class Agent:
            def __init__(self):
                from mem0 import Memory
                self.memory = Memory()

            def remember(self, text):
                self.memory.add(text)
        """
    ) == [("self.memory.add", "mem0.Memory")]


def test_langgraph_store_annotation_and_get_store() -> None:
    assert calls(
        """
        from typing import Optional
        from langgraph.config import get_store
        from langgraph.store.base import BaseStore

        def node(state, *, store: Optional[BaseStore] = None):
            store.put(("notes",), "k", {"v": 1})

        def other(state, store: "BaseStore"):
            store.put(("a",), "k", {})

        def third(state):
            s = get_store()
            s.put(("b",), "k", {})
        """
    ) == [
        ("store.put", "langgraph.store.base.BaseStore"),
        ("store.put", "langgraph.store.base.BaseStore"),
        ("s.put", "langgraph.config.get_store"),
    ]


def test_factory_collections_aliases_and_vectorstores() -> None:
    found = calls(
        """
        import chromadb
        from langchain_community.vectorstores import FAISS
        from qdrant_client import QdrantClient

        col = chromadb.PersistentClient(path="db").get_or_create_collection("c")
        col.add(ids=["1"], documents=["x"])
        db = FAISS.from_texts(["a"], emb)
        db2 = db
        db2.add_texts(["b"])
        QdrantClient(":memory:").upsert("c", points=[])
        """
    )
    assert [c for c, _ in found] == ["col.add", "db2.add_texts", "QdrantClient(':memory:').upsert"]


def test_retract_adapter_write_is_flagged_but_sdk_calls_are_not() -> None:
    assert calls(
        """
        from retract import Retract
        from retract.adapters import SimpleVectorStore

        store = SimpleVectorStore()
        rt = Retract("lineage.db")
        rt.add("fact", tier="shared")
        store.write("fact", {})
        """
    ) == [("store.write", "retract.adapters.SimpleVectorStore")]


def test_no_false_positives_on_unrelated_add_and_reads() -> None:
    assert (
        calls(
            """
        from mem0 import Memory

        seen = set()
        seen.add(1)
        memory = Memory()
        memory.search("q", user_id="u")
        memory.get_all(user_id="u")
        Memory.add  # attribute access, not a call
        """
        )
        == []
    )


def test_allowlist_exit_codes_and_parse_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.retract.audit]\nallow = ["vendor/*"]\n')
    bad = "from mem0 import Memory\nMemory().add('x')\n"
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "client.py").write_text(bad)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text(bad)
    (tmp_path / "app.py").write_text("x = 1\n")
    assert main(["audit", str(tmp_path)]) == 0

    (tmp_path / "agent.py").write_text(bad)
    assert main(["audit", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "agent.py:2:1: likely bypass: Memory().add() on mem0.Memory" in out
    assert "vendor" not in out and ".venv" not in out

    (tmp_path / "agent.py").write_text("def broken(:\n")
    assert main(["audit", str(tmp_path)]) == 1  # unparseable files never pass silently
    assert "cannot parse" in capsys.readouterr().err

    assert main(["audit", str(tmp_path / "missing")]) == 2


def test_bad_allowlist_type_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.retract.audit]\nallow = "vendor/*"\n')
    with pytest.raises(ValueError, match="list of str"):
        audit(tmp_path)


def test_repository_audits_clean() -> None:
    findings, errors = audit(ROOT)
    assert (findings, errors) == ([], [])
