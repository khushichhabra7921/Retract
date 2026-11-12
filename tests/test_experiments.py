"""M8 harness: one paired priya seed through every config, then aggregation."""

import pytest

pytest.importorskip("langgraph")

from experiments.configs import RUNS, run_trial  # noqa: E402
from experiments.run import check_pairing, markdown, summarize  # noqa: E402


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RETRACT_PSEUDONYM_KEY", "test-pseudonym-key")
        mp.setenv("RETRACT_SIGNING_KEY", "test-signing-key")
        return [run_trial("priya", 1, c, tmp_path_factory.mktemp(c)) for c in RUNS]


def by_config(rows):
    return {r["config"]: r for r in rows}


def test_configs_behave_as_designed(rows):
    r = by_config(rows)
    residue = {c: r[c]["after"]["raw_items_with_target"]["not_quarantined"] for c in RUNS}
    assert residue["naive_delete"] > 0  # derived copies survive a naive delete
    assert residue["clean"] == residue["delete"] == residue["auto"] == residue["quarantine"] == 0
    assert r["naive_delete"]["after"]["probes"]["guarded"] > 0
    assert all(r[c]["after"]["probes"]["guarded"] == 0 for c in ("delete", "auto", "quarantine"))
    assert r["quarantine"]["after"]["probes"]["raw_quarantined"] > 0  # hidden, not purged
    assert r["auto"]["op"]["outcome"] == "completed" and r["auto"]["op"]["receipt_ok"]
    assert r["quarantine"]["op"]["receipt_type"] == "containment"
    # auto rebuilds what delete throws away
    assert r["auto"]["after"]["readable_records"] > r["delete"]["after"]["readable_records"]
    assert r["auto"]["after"]["honest_mentions"] > r["delete"]["after"]["honest_mentions"]


def test_pairing_and_summary(rows):
    check_pairing(rows)  # same seed, same world before retraction
    broken = [dict(r) for r in rows]
    broken[1] = {**broken[1], "before": {**broken[1]["before"], "readable_records": -1}}
    with pytest.raises(RuntimeError, match="unpaired trial"):
        check_pairing(broken)

    s = summarize(rows)["scenarios"]["priya"]
    assert s["naive_delete"]["structural_residue"]["k"] == 1
    assert s["auto"]["structural_residue"]["k"] == 0
    assert "clopper_pearson95_upper_one_sided" in s["auto"]["structural_residue"]
    assert s["unretracted"]["answer_contamination"]["mean"] > 0
    assert s["auto"]["outcomes"] == {"completed": 1}
    assert "| auto | 0/1" in markdown(summarize(rows))
