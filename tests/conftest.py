import pytest

from retract import Retract
from retract.adapters.simple import SimpleVectorStore

TIERS = ("private:support", "private:planning", "shared")


@pytest.fixture(autouse=True)
def pseudonym_key(monkeypatch):
    monkeypatch.setenv("RETRACT_PSEUDONYM_KEY", "test-pseudonym-key")


@pytest.fixture
def rt(tmp_path):
    r = Retract(tmp_path / "lineage.db", stores={t: SimpleVectorStore() for t in TIERS})
    yield r
    r.close()
