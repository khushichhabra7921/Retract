"""MemoryAdapter contract. Every adapter runs this suite; Mem0 joins as an integration case."""

import pytest

from retract.adapters.mem0 import Mem0Adapter, local_config
from retract.adapters.simple import SimpleVectorStore

ADAPTERS = [
    pytest.param(lambda tmp: SimpleVectorStore(), id="simple"),
    pytest.param(
        lambda tmp: Mem0Adapter(local_config(tmp)), id="mem0", marks=pytest.mark.integration
    ),
]


@pytest.fixture(params=ADAPTERS)
def store(request, tmp_path):
    s = request.param(tmp_path)
    yield s
    if hasattr(s, "close"):
        s.close()


def meta(n):
    return {"retract_token": f"write:r{n}", "record_id": f"r{n}"}


def test_write_get_roundtrip_keeps_metadata(store):
    aid = store.write("Priya prefers WhatsApp.", meta(1))
    item = store.get(aid)
    assert item.adapter_record_id == aid
    assert item.content == "Priya prefers WhatsApp."
    assert item.metadata["retract_token"] == "write:r1" and item.metadata["record_id"] == "r1"
    assert store.contains(aid)


def test_search_ranks_best_match_first(store):
    store.write("Arjun's renewal is due in March.", meta(1))
    target = store.write("Priya prefers WhatsApp for follow-ups.", meta(2))
    hits = store.search("whatsapp follow-ups", k=2)
    assert hits[0].adapter_record_id == target
    assert len(store.search("anything", k=1)) == 1


def test_delete_makes_item_absent(store):
    aid = store.write("x", meta(1))
    store.delete(aid)
    assert store.get(aid) is None
    assert not store.contains(aid)
    assert store.find_by_token("write:r1") is None
    assert all(h.adapter_record_id != aid for h in store.search("x", k=10))
    store.delete(aid)  # deleting a missing id is a no-op


def test_find_by_token(store):
    aid = store.write("x", meta(1))
    store.write("y", meta(2))
    assert store.find_by_token("write:r1") == aid
    assert store.find_by_token("write:missing") is None


def test_list_ids_when_supported(store):
    if not store.supports_list_ids:
        pytest.skip("adapter does not enumerate ids")
    ids = {store.write(str(n), meta(n)) for n in range(3)}
    assert set(store.list_ids()) == ids


def test_capability_flags_are_bools(store):
    assert isinstance(store.idempotent_put, bool)
    assert isinstance(store.supports_list_ids, bool)
