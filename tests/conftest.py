import pytest

from retract import Retract
from retract.adapters.simple import SimpleVectorStore

TIERS = ("private:support", "private:planning", "shared")


@pytest.fixture
def rt(tmp_path):
    r = Retract(tmp_path / "lineage.db", stores={t: SimpleVectorStore() for t in TIERS})
    yield r
    r.close()
