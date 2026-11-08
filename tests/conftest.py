import pytest

from retract import Retract
from retract.adapters.simple import SimpleVectorStore
from retract.derive.fakes import FakeLLM
from testbed.slice import TIERS, build


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("RETRACT_PSEUDONYM_KEY", "test-pseudonym-key")
    monkeypatch.setenv("RETRACT_SIGNING_KEY", "test-signing-key")


@pytest.fixture
def rt(tmp_path):
    r = Retract(tmp_path / "lineage.db", stores={t: SimpleVectorStore() for t in TIERS})
    yield r
    r.close()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def world(rt, llm):
    return build(rt, llm)
