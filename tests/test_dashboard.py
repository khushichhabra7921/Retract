"""M7: dashboard smoke tests on the demo world (TestClient, no server)."""

import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from retract.dashboard.app import create_app  # noqa: E402
from retract.dashboard.demo import build  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(lambda: build(tmp_path))
    with TestClient(app) as c:  # runs the lifespan, so the Retract instance is closed after
        yield c


def failed_op(client):
    return re.search(r"/ops/([0-9a-f]{32})", client.get("/ops").text).group(1)


def csrf(html):
    return re.search(r'name="csrf"\s+value="([^"]+)"', html).group(1)


def closure_hash(client, source):
    page = client.get("/retract", params={"source": source})
    assert page.status_code == 200
    return re.search(r'name="closure_hash" value="([^"]+)"', page.text).group(1)


def test_pages_render(client):
    records = client.get("/")
    assert records.status_code == 200 and "Priya prefers WhatsApp" in records.text
    assert "held</span>" in records.text  # the failed op still holds its records
    derived = re.search(r'/records/([0-9a-f]+)"><code>\w+</code></a>\s*<span class="muted">derived',
                        records.text).group(1)  # fmt: skip
    record = client.get(f"/records/{derived}")
    assert "Why does the agent know this?" in record.text and "primary, owned by" in record.text
    assert client.get("/sources").text.count("user:u_") >= 3
    assert "failed" in client.get(f"/ops/{failed_op(client)}").text
    assert client.get("/records/nope").status_code == client.get("/ops/nope").status_code == 404


def test_post_without_valid_csrf_is_rejected(client):
    op = failed_op(client)
    assert client.post(f"/ops/{op}/resume").status_code == 403
    assert client.post(f"/ops/{op}/resume", data={"csrf": "guess"}).status_code == 403
    form = {"source": "user:u_priya", "mode": "auto", "closure_hash": "0" * 64}
    assert client.post("/retract", data=form).status_code == 403
    assert "Resume operation" in client.get(f"/ops/{op}").text  # nothing happened


def test_failed_op_blocks_then_resumes_then_retraction_completes(client):
    op = failed_op(client)
    token = csrf(client.get(f"/ops/{op}").text)
    form = {"csrf": token, "source": "user:u_priya", "mode": "auto"}

    blocked = client.post(
        "/retract", data={**form, "closure_hash": closure_hash(client, "user:u_priya")},
        follow_redirects=False,
    )  # fmt: skip
    assert blocked.status_code == 303 and blocked.headers["location"] == f"/ops/{op}?blocked=1"

    resumed = client.post(f"/ops/{op}/resume", data={"csrf": token}, follow_redirects=False)
    assert resumed.status_code == 303
    page = client.get(f"/ops/{op}").text
    assert "badge completed" in page and "Resume operation" not in page

    stale = client.post("/retract", data={**form, "closure_hash": "0" * 64})
    assert stale.status_code == 409 and "changed since your preview" in stale.text

    done = client.post(
        "/retract", data={**form, "closure_hash": closure_hash(client, "user:u_priya")}
    )
    assert done.status_code == 200 and "badge completed" in done.text
    assert "badge retraction" in done.text and "answer contamination" in done.text
    receipt = client.get(f"{done.url.path}/receipt.json")
    assert receipt.json()["body"]["outcome"] == "completed"
    assert "WhatsApp" not in receipt.text  # receipts carry no raw content
