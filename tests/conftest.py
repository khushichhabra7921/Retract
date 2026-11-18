import os
import uuid

import pytest

from retract import Retract
from retract.adapters.simple import SimpleVectorStore
from retract.derive.fakes import FakeLLM
from testbed.slice import TIERS, build


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("RETRACT_PSEUDONYM_KEY", "test-pseudonym-key")
    monkeypatch.setenv("RETRACT_SIGNING_KEY", "test-signing-key")


# Set to a Postgres URL (e.g. postgresql://postgres@127.0.0.1:5432/postgres) to also run every
# lineage test against Postgres. Each test gets its own schema, dropped afterwards.
PG_URL = os.environ.get("RETRACT_TEST_PG")


@pytest.fixture(params=["sqlite", "postgres"] if PG_URL else ["sqlite"])
def db(request, tmp_path):
    """Lineage target: a SQLite file, or a fresh Postgres schema."""
    if request.param == "sqlite":
        yield tmp_path / "lineage.db"
        return
    import psycopg

    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    yield f"{PG_URL}{'&' if '?' in PG_URL else '?'}options=-csearch_path%3D{schema}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def rt(db):
    r = Retract(db, stores={t: SimpleVectorStore() for t in TIERS})
    yield r
    r.close()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def world(rt, llm):
    return build(rt, llm)
