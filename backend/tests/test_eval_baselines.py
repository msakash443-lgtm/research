"""Plan X.5: no prompt version ships without an evaluation baseline, and a baseline must match the current
gold set. CI never calls a model; it checks that a person ran the evaluation (evals/README.md)."""

import json
from pathlib import Path

import pytest

from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.extraction_schema import ExtractionSchema, FieldSpec, load_schema, schema_version
from app.prescreen import PrescreenError, Suggestion
from evals import baselines as bl
from evals.run_eval import (
    extraction_dataset_hash, extraction_gold, extraction_hash_for, load_criteria, load_gold, run_extraction,
    run_screening, screening_dataset_hash,
)

# ---- the rule, on the real files --------------------------------------------------------------------


def test_every_prompt_the_app_uses_has_a_valid_baseline():
    gold = load_gold()
    criteria = load_criteria()
    live = set(bl.live_prompt_refs())
    found = bl.problems(
        json.loads(bl.BASELINES.read_text(encoding="utf-8")), live,
        has_gold=bool(gold["papers"]), has_extraction_gold=bool(extraction_gold(gold)),
        current_hash=screening_dataset_hash(gold, criteria), extraction_hash=extraction_hash_for(gold),
        report_dir=bl.HERE / "reports",
    )
    assert found == []


def test_the_real_extraction_hash_follows_the_schema_a_report_names():
    current = extraction_hash_for(EXTRACTION_GOLD)
    default = load_schema("default")
    assert current(schema_version(default)) == extraction_dataset_hash(EXTRACTION_GOLD, default)
    assert current(f"default@{default.version + 1}") is None  # a newer or older version of the schema
    assert current("no-such-schema@1") is None


def test_the_extraction_prompt_is_registered_as_an_extraction_eval():
    baselines = json.loads(bl.BASELINES.read_text(encoding="utf-8"))
    assert baselines["evidence_extraction@1"]["eval"] == "extraction"


def test_live_prompts_are_discovered_from_the_code():
    assert {"screening_prescreen@2", "evidence_synthesis@4", "synonym_suggestions@1"} <= set(bl.live_prompt_refs())


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

    assert report["prompt"] == "screening_prescreen@2" and report["model_id"] == "fake-model"
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


def test_cli_refuses_an_empty_gold_set_for_either_step(capsys):
    from evals.run_eval import main

    assert main(["screening"]) == 2 and "empty" in capsys.readouterr().err
    assert main(["extraction"]) == 2 and "no verified extractions" in capsys.readouterr().err


# ---- the extraction runner (X.103), with the model replaced -------------------------------------------

EXTRACTION_SCHEMA = ExtractionSchema(name="brief", version=1, label="Brief", description="", fields=[
    FieldSpec(key="design", type="string", description="Study design.", critical=True),
    FieldSpec(key="sample_size", type="integer", description="Participants.", critical=True),
])
EXTRACTION_GOLD = {"topic": "test", "supplied_by": "test fixture", "papers": [
    {"id": "a", "title": "Paper A", "abstract": "We surveyed 120 students.", "label": "include", "reason": "fits",
     "extraction": {"design": {"value": "survey", "source_quote": "surveyed"},
                    "sample_size": {"value": 120, "source_quote": "120 students"}}},
    {"id": "b", "title": "Paper B", "abstract": "A trial of 40 adults.", "label": "include", "reason": "fits",
     "extraction": {"design": {"value": "trial", "source_quote": "trial"},
                    "sample_size": {"value": 40, "source_quote": "40 adults"}}},
    {"id": "c", "title": "Paper C", "abstract": "No gold extraction for this one.", "label": "exclude", "reason": "wrong"},
]}


class FakeExtractionLLM:
    """Stands in for the model's complete_json: returns the next queued reply, or raises it."""

    _model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, system, user, schema, max_tokens=None):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _extracted(design, size):
    return {
        "design": {"value": design, "evidence": {"quote": design, "page": 1, "section": None}, "confidence": 0.9},
        "sample_size": {"value": size, "evidence": {"quote": str(size), "page": 1, "section": None}, "confidence": 0.9},
    }


def test_the_extraction_runner_sends_the_real_prompt_scores_gold_papers_and_records_failures():
    llm = FakeExtractionLLM([_extracted("survey", 120), LLMResponseError("timeout")])
    report = run_extraction(EXTRACTION_GOLD, EXTRACTION_SCHEMA, llm)

    assert llm.calls == 2  # paper c has no gold extraction, so the model never sees it
    assert report["kind"] == "extraction" and report["prompt"] == "evidence_extraction@1"
    assert report["model_id"] == "fake-model"
    assert report["dataset_sha256"] == extraction_dataset_hash(EXTRACTION_GOLD, EXTRACTION_SCHEMA)
    assert report["result"]["status"] == "incomplete" and report["result"]["failed"] == ["b"]
    assert report["result"]["fields"]["sample_size"]["tp"] == 1
    assert [(i["id"], i["error"]) for i in report["items"]] == [("a", None), ("b", "timeout")]
    assert "Paper A" not in json.dumps(report)  # ids and outcomes only, as for screening


def test_a_wrong_critical_value_fails_the_extraction_run():
    llm = FakeExtractionLLM([_extracted("survey", 120), _extracted("cohort", 40)])
    report = run_extraction(EXTRACTION_GOLD, EXTRACTION_SCHEMA, llm)
    assert report["result"]["status"] == "failed"


def test_no_model_configured_stops_the_extraction_run():
    llm = FakeExtractionLLM([LLMConfigurationError("not connected")] * 2)
    with pytest.raises(LLMConfigurationError):
        run_extraction(EXTRACTION_GOLD, EXTRACTION_SCHEMA, llm)


def test_only_papers_with_a_verified_extraction_are_gold_for_the_extraction_eval():
    assert list(extraction_gold(EXTRACTION_GOLD)) == ["a", "b"]
    assert extraction_gold(GOLD) == {}


def test_an_extraction_baseline_needs_a_report_once_there_are_extraction_gold_papers(tmp_path):
    live = {"evidence_extraction@1"}
    entry = {"eval": "extraction", "status": "unevaluated", "reason": "no gold extractions yet"}
    assert bl.problems({"evidence_extraction@1": entry}, live, has_gold=True, has_extraction_gold=False,
                       current_hash="h", report_dir=tmp_path) == []
    assert bl.problems({"evidence_extraction@1": entry}, live, has_gold=False, has_extraction_gold=True,
                       current_hash="h", report_dir=tmp_path) == [
        "evidence_extraction@1: the gold set has data now; run the evaluation (status is 'unevaluated')"
    ]


def test_an_extraction_baseline_points_at_an_extraction_report(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(
        {"kind": "screening", "prompt": "evidence_extraction@1", "dataset_sha256": "h", "result": {"status": "passed"}}),
        encoding="utf-8")
    entry = {"eval": "extraction", "status": "passed", "report": "r.json"}
    found = bl.problems({"evidence_extraction@1": entry}, {"evidence_extraction@1"}, has_gold=False,
                        has_extraction_gold=True, current_hash="h", report_dir=tmp_path)
    assert any("is for screening evidence_extraction@1" in p for p in found)


def _extraction_check(tmp_path, gold, report_name="x.json"):
    entry = {"eval": "extraction", "status": "passed", "report": report_name}
    return bl.problems(
        {"evidence_extraction@1": entry}, {"evidence_extraction@1"}, has_gold=True, has_extraction_gold=True,
        current_hash=screening_dataset_hash(gold, CRITERIA),
        extraction_hash=lambda schema: extraction_dataset_hash(gold, EXTRACTION_SCHEMA) if schema == "brief@1" else None,
        report_dir=tmp_path,
    )


def test_a_fresh_extraction_report_satisfies_the_rule_and_a_changed_gold_set_invalidates_it(tmp_path):
    report = run_extraction(EXTRACTION_GOLD, EXTRACTION_SCHEMA,
                            FakeExtractionLLM([_extracted("survey", 120), _extracted("trial", 40)]))
    assert report["result"]["status"] == "passed"
    (tmp_path / "x.json").write_text(json.dumps(report), encoding="utf-8")

    assert _extraction_check(tmp_path, EXTRACTION_GOLD) == []
    a, *rest = EXTRACTION_GOLD["papers"]
    changed = {**EXTRACTION_GOLD, "papers": [
        {**a, "extraction": {**a["extraction"], "sample_size": {"value": 121, "source_quote": "120 students"}}}, *rest]}
    assert any("different gold set" in p for p in _extraction_check(tmp_path, changed))


def test_an_extraction_report_for_a_schema_the_app_does_not_have_does_not_count(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps(
        {"kind": "extraction", "prompt": "evidence_extraction@1", "schema": "gone@3", "dataset_sha256": "h",
         "result": {"status": "passed"}}), encoding="utf-8")
    assert _extraction_check(tmp_path, EXTRACTION_GOLD) == [
        "evidence_extraction@1: report x.json names extraction schema 'gone@3', which the app does not have; re-run it"
    ]


def test_criteria_file_shape_is_checked(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps([{"code": "I1", "kind": "maybe", "text": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_criteria(bad)
    assert load_criteria() == []  # nothing invented while Q10 is open
