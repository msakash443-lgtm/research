"""Plan X.5: no prompt version ships without an evaluation baseline, and a baseline must match the current
gold set. CI never calls a model; it checks that a person ran the evaluation (evals/README.md)."""

import json
from pathlib import Path

import pytest

from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.prescreen import PrescreenError, Suggestion
from evals import baselines as bl
from evals.run_eval import load_criteria, load_gold, run_screening, screening_dataset_hash

# ---- the rule, on the real files --------------------------------------------------------------------


def test_every_prompt_the_app_uses_has_a_valid_baseline():
    gold = load_gold()
    criteria = load_criteria()
    live = set(bl.live_prompt_refs())
    found = bl.problems(
        json.loads(bl.BASELINES.read_text(encoding="utf-8")), live,
        has_gold=bool(gold["papers"]), current_hash=screening_dataset_hash(gold, criteria),
        report_dir=bl.HERE / "reports",
    )
    assert found == []


def test_live_prompts_are_discovered_from_the_code():
    assert {"screening_prescreen@1", "evidence_synthesis@4", "synonym_suggestions@1"} <= set(bl.live_prompt_refs())


def test_a_load_prompt_call_the_rule_cannot_see_is_an_error(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "sneaky.py").write_text('p = load_prompt("x", 2)\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"load_prompt\(\*CONSTANT\)"):
        bl.live_prompt_refs(tmp_path / "app")


# ---- the rule, on made-up baselines (these are test inputs, not gold data) ---------------------------

LIVE = {"screening_prescreen@1", "evidence_synthesis@4"}
NONE_ENTRY = {"eval": "none", "reason": "no gold type"}


def _report(tmp_path, name="r.json", **over):
    body = {"kind": "screening", "prompt": "screening_prescreen@1", "dataset_sha256": "h1", "result": {"status": "passed"}}
    body.update(over)
    (tmp_path / name).write_text(json.dumps(body), encoding="utf-8")
    return name


def _check(tmp_path, screening_entry, *, has_gold=True, current_hash="h1", extra=None):
    baselines = {"screening_prescreen@1": screening_entry, "evidence_synthesis@4": NONE_ENTRY, **(extra or {})}
    return bl.problems(baselines, LIVE, has_gold=has_gold, current_hash=current_hash, report_dir=tmp_path)


def test_a_fresh_passing_report_satisfies_the_rule(tmp_path):
    assert _check(tmp_path, {"eval": "screening", "status": "passed", "report": _report(tmp_path)}) == []


def test_a_new_prompt_version_without_an_entry_fails(tmp_path):
    found = bl.problems({"evidence_synthesis@4": NONE_ENTRY}, LIVE, has_gold=False, current_hash="h", report_dir=tmp_path)
    assert found == ["screening_prescreen@1: no baseline entry; run the evaluation (or add an eval 'none' entry with a reason)"]


def test_an_entry_for_a_retired_prompt_version_fails(tmp_path):
    found = _check(tmp_path, {"eval": "screening", "status": "unevaluated", "reason": "empty"}, has_gold=False,
                   extra={"screening_prescreen@0": NONE_ENTRY})
    assert any("no longer uses" in p for p in found)


def test_a_changed_gold_set_or_criteria_invalidates_the_report(tmp_path):
    found = _check(tmp_path, {"eval": "screening", "status": "passed", "report": _report(tmp_path)}, current_hash="h2")
    assert any("different gold set" in p for p in found)


def test_a_report_for_another_prompt_version_does_not_count(tmp_path):
    name = _report(tmp_path, prompt="screening_prescreen@0")
    assert any("is for screening screening_prescreen@0" in p for p in _check(tmp_path, {"eval": "screening", "status": "passed", "report": name}))


def test_unevaluated_is_only_allowed_while_there_is_no_gold_data(tmp_path):
    entry = {"eval": "screening", "status": "unevaluated", "reason": "gold set empty"}
    assert _check(tmp_path, entry, has_gold=False) == []
    assert any("run the evaluation" in p for p in _check(tmp_path, entry, has_gold=True))


def test_a_failed_baseline_needs_a_person_to_accept_it(tmp_path):
    name = _report(tmp_path, result={"status": "failed"})
    assert any("accepted_by" in p for p in _check(tmp_path, {"eval": "screening", "status": "failed", "report": name}))
    accepted = {"eval": "screening", "status": "failed", "report": name, "accepted_by": "owner", "reason": "known, tracked in plan"}
    assert _check(tmp_path, accepted) == []


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"eval": "screening", "status": "passed", "report": "missing.json"}, "not found"),
        ({"eval": "screening", "status": "passed"}, "not found"),
        ({"eval": "nonsense"}, "eval must be one of"),
    ],
)
def test_broken_entries_are_reported(tmp_path, entry, expected):
    assert any(expected in p for p in _check(tmp_path, entry))


def test_eval_none_needs_a_reason(tmp_path):
    found = _check(tmp_path, {"eval": "screening", "status": "unevaluated", "reason": "x"}, has_gold=False,
                   extra={"evidence_synthesis@4": {"eval": "none", "reason": " "}})
    assert found == ["evidence_synthesis@4: eval 'none' needs a reason"]


def test_a_baseline_status_must_match_its_report(tmp_path):
    name = _report(tmp_path, result={"status": "incomplete"})
    assert any("report says 'incomplete'" in p for p in _check(tmp_path, {"eval": "screening", "status": "passed", "report": name}))


# ---- the runner, with the model replaced ---------------------------------------------------------------

GOLD = {"topic": "test", "supplied_by": "test fixture", "papers": [
    {"id": "a", "title": "Paper A", "abstract": "x", "label": "include", "reason": "fits"},
    {"id": "b", "title": "Paper B", "abstract": None, "label": "exclude", "reason": "wrong population"},
    {"id": "c", "title": "Paper C", "abstract": "y", "label": "include", "reason": "fits"},
]}
CRITERIA = [{"code": "I1", "kind": "include", "text": "Adults"}, {"code": "E1", "kind": "exclude", "text": "Children"}]


class FakeLLM:
    pass


def _prescreen(answers):
    def fake(llm, criteria, source, *, min_confidence, prompt):
        answer = answers[source.title]
        if isinstance(answer, Exception):
            raise answer
        return Suggestion(answer, None, 0.9, "because", "fake-model", prompt.ref, ())
    return fake


def test_the_runner_scores_every_paper_and_records_failures_instead_of_filling_them_in():
    answers = {"Paper A": "include", "Paper B": PrescreenError("bad reason code"), "Paper C": "exclude"}
    report = run_screening(GOLD, CRITERIA, FakeLLM(), min_confidence=0.6, prescreen=_prescreen(answers))

    assert report["prompt"] == "screening_prescreen@1" and report["model_id"] == "fake-model"
    assert report["dataset_sha256"] == screening_dataset_hash(GOLD, CRITERIA)
    assert report["result"]["status"] == "incomplete" and report["result"]["failed"] == 1
    assert report["result"]["false_exclusions"] == ["c"]
    assert [(i["id"], i["ai"], i["error"]) for i in report["items"]] == [
        ("a", "include", None), ("b", None, "bad reason code"), ("c", "exclude", None)
    ]
    assert "Paper A" not in json.dumps(report)  # ids only: no titles or abstracts in a shareable report


def test_a_model_error_on_one_paper_is_recorded_too():
    answers = {"Paper A": "include", "Paper B": "exclude", "Paper C": LLMResponseError("timeout")}
    report = run_screening(GOLD, CRITERIA, FakeLLM(), min_confidence=0.6, prescreen=_prescreen(answers))
    assert report["items"][2]["error"] == "timeout" and report["result"]["status"] == "incomplete"


def test_no_model_configured_stops_the_run_instead_of_reporting_all_failures():
    answers = {t: LLMConfigurationError("not connected") for t in ("Paper A", "Paper B", "Paper C")}
    with pytest.raises(LLMConfigurationError):
        run_screening(GOLD, CRITERIA, FakeLLM(), min_confidence=0.6, prescreen=_prescreen(answers))


def test_the_dataset_hash_changes_with_labels_and_criteria():
    base = screening_dataset_hash(GOLD, CRITERIA)
    relabelled = {**GOLD, "papers": [{**GOLD["papers"][0], "label": "exclude"}, *GOLD["papers"][1:]]}
    assert screening_dataset_hash(relabelled, CRITERIA) != base
    assert screening_dataset_hash(GOLD, CRITERIA[:1]) != base
    assert screening_dataset_hash({**GOLD, "papers": list(reversed(GOLD["papers"]))}, CRITERIA) == base  # order-free


def test_cli_refuses_an_empty_gold_set_and_the_missing_extraction_step(capsys):
    from evals.run_eval import main

    assert main(["screening"]) == 2 and "empty" in capsys.readouterr().err
    assert main(["extraction"]) == 2 and "M3.3" in capsys.readouterr().err


def test_criteria_file_shape_is_checked(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps([{"code": "I1", "kind": "maybe", "text": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_criteria(bad)
    assert load_criteria() == []  # nothing invented while Q10 is open
