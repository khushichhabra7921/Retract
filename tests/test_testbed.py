"""M5 acceptance: LangGraph testbed scenarios with FakeLLM (Mem0 variant is integration-only)."""

import json

import httpx
import pytest

pytest.importorskip("langgraph")

from retract.derive import prompts  # noqa: E402
from retract.derive.cache import ResponseCache  # noqa: E402
from retract.derive.fakes import FakeLLM  # noqa: E402
from retract.derive.groq import GroqLLM  # noqa: E402
from testbed.run import main  # noqa: E402
from testbed.scenarios import run_scenario  # noqa: E402


def gone(after):
    return (
        after["answers_with_target"] == 0
        and after["readable_records_with_target"] == 0
        and after["raw_items_with_target"]["not_quarantined"] == 0
    )


def test_priya_scenario(tmp_path):
    s = run_scenario("priya", FakeLLM(), tmp_path, store="simple")
    assert s["receipt_ok"] and s["outcome"] == "completed"
    assert s["verification"]["retrieval_residue"]["status"] == "pass"
    assert s["before"]["answers_with_target"] > 0 and gone(s["after"])
    assert "3 agents" in s["blast_radius"]  # support, research and planning memories all hit
    memory_log = (tmp_path / "memory.jsonl").read_text(encoding="utf-8")
    assert memory_log and "WhatsApp" not in memory_log  # memory channel logs ids, not content
    assert s["message_log_lines_with_target"] > 0  # message channel is outside Retract's scope
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == s


def test_poisoned_doc_four_stages(tmp_path):
    s = run_scenario("poisoned_doc", FakeLLM(), tmp_path, store="simple")
    st = s["stages"]
    assert st["entered_memory"] == 1  # 1. the doc itself
    assert {"support/shared", "research/private:research", "planning/private:planning"} <= set(
        st["retrieved_or_promoted"]
    )  # 2. promoted to the shared tier and picked up by both other agents
    assert st["influenced_answers"] > 0  # 3.
    assert st["influence_gone"] and gone(s["after"])  # 4.
    assert s["receipt_ok"]


def test_cli_runs_a_scenario(tmp_path, capsys):
    assert main(["--scenario", "priya", "--store", "simple", "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scenario"] == "priya" and out["llm"]["name"] == "fake"


def fake_groq(request):
    """Groq stand-in backed by FakeLLM, so a "live" run is offline and deterministic."""
    msgs = json.loads(request.content)["messages"]
    system, user = msgs[0]["content"], msgs[1]["content"]
    if system == prompts.SUMMARIZE:
        text = FakeLLM().summarize([line[2:] for line in user.split("\n")])
    else:
        ctx, question = user.rsplit("\n\nQuestion: ", 1)
        contexts = [line[2:] for line in ctx.split("\n")[1:] if line.startswith("- ")]
        text = FakeLLM().answer(question, contexts)
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_groq_run_replays_from_cache_without_key_or_network(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache.jsonl"
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = httpx.Client(transport=httpx.MockTransport(fake_groq))
    live = GroqLLM(client=client, cache=ResponseCache(cache))
    s = run_scenario("priya", live, tmp_path / "live", store="simple")
    assert live.calls > 0 and s["outcome"] == "completed"

    # A fresh process: new ephemeral keys, no Groq key. A replay miss would raise.
    monkeypatch.setenv("RETRACT_PSEUDONYM_KEY", "other-pseudonym-key")
    monkeypatch.setenv("RETRACT_SIGNING_KEY", "other-signing-key")
    monkeypatch.delenv("GROQ_API_KEY")
    args = ["--scenario", "priya", "--store", "simple", "--llm", "groq", "--replay"]
    assert main([*args, "--cache", str(cache), "--out", str(tmp_path / "replay")]) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed.pop("llm") == {
        "name": "groq", "calls": 0, "cache_hits": live.calls + live.cache_hits,
        "replay": True, "model": "openai/gpt-oss-120b",
    }  # fmt: skip
    assert replayed == s

    with pytest.raises(SystemExit):
        main(["--scenario", "priya", "--replay"])  # replay without --llm groq


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["priya", "poisoned_doc"])
def test_scenarios_with_mem0_research_memory(tmp_path, scenario):
    s = run_scenario(scenario, FakeLLM(), tmp_path, store="mem0")
    assert s["research_store"] == "mem0" and s["receipt_ok"] and gone(s["after"])
