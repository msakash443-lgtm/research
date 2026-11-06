"""AI accuracy against the project's own human decisions and verified extractions (X.80)."""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.accuracy_report import extraction_accuracy, screening_accuracy
from app.database import SessionLocal
from app.main import app
from app.models import Extraction, ScreeningDecision, Source

# ---- pure -------------------------------------------------------------------------------------------------


def test_screening_recall_precision_false_exclusions_and_skipped_maybes():
    pairs = [("include", "include"), ("maybe", "include"), ("exclude", "include"),  # person included three
             ("include", "exclude"), ("exclude", "exclude"),                          # person excluded two
             ("include", "maybe")]                                                    # no label either way
    out = screening_accuracy(pairs)

    assert out["n"] == 5 and out["skipped_human_maybe"] == 1
    assert out["recall"] == 0.333 and out["recall_not_excluded"] == 0.667
    assert out["precision"] == 0.5 and out["false_exclusions"] == 1
    assert out["matrix"]["include"] == {"include": 1, "maybe": 1, "exclude": 1}


def test_a_zero_denominator_is_null_never_zero():
    out = screening_accuracy([("exclude", "exclude"), ("maybe", "exclude")])
    assert out["recall"] is None and out["recall_not_excluded"] is None and out["precision"] is None
    assert screening_accuracy([])["n"] == 0 and screening_accuracy([])["recall"] is None


def test_extraction_fields_are_scored_per_field_with_the_critical_ones_marked():
    items = [
        ({"sample": {"n": 10}, "key_findings": "A", "limitations": None}, {"sample": {"n": 10}, "key_findings": "B", "limitations": None}),
        ({"sample": {"n": 20}, "key_findings": "C", "limitations": "small"}, {"sample": {"n": 20}, "key_findings": "C", "limitations": None}),
    ]
    out = extraction_accuracy(items, ["sample", "key_findings"])

    assert out["papers"] == 2
    sample, findings, limitations = out["fields"]["sample"], out["fields"]["key_findings"], out["fields"]["limitations"]
    assert (sample["tp"], sample["fp"], sample["fn"], sample["precision"], sample["recall"], sample["critical"]) == (2, 0, 0, 1.0, 1.0, True)
    assert (findings["tp"], findings["fp"], findings["fn"], findings["precision"], findings["recall"]) == (1, 1, 1, 0.5, 0.5)
    assert (limitations["tp"], limitations["fn"], limitations["precision"], limitations["recall"], limitations["critical"]) == (0, 1, None, 0.0, False)


# ---- API --------------------------------------------------------------------------------------------------


def _world(label):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"acc-{label}-{uuid.uuid4().hex[:8]}@example.com", "display_name": "A"})
    return client, client.post("/api/projects", json={"title": "Accuracy"}).json()["id"]


def _source(client, pid, title):
    return client.post(f"/api/projects/{pid}/sources", json={"title": title}).json()["id"]


def _decide(pid, source_id, stage, ai, human):
    with SessionLocal() as db:
        for seq, (by, decision) in enumerate((("ai", ai), ("human", human)), 1):
            if decision is None:
                continue
            db.add(ScreeningDecision(project_id=uuid.UUID(pid), source_id=uuid.UUID(source_id), stage=stage, seq=seq, decision=decision,
                                     reason_code=None, decided_by=by, decider="agent:prescreen" if by == "ai" else "tester", confidence=0.9))
        db.commit()


def test_the_screening_report_counts_only_sources_with_both_an_ai_suggestion_and_a_person_decision():
    client, pid = _world("scr")
    for n, (ai, human) in enumerate([("include", "include"), ("maybe", "include"), ("exclude", "include"), ("include", "exclude"),
                                     ("exclude", "exclude"), ("include", "maybe"), ("include", None)]):
        _decide(pid, _source(client, pid, f"Paper {n}"), "title_abstract", ai, human)

    body = client.get(f"/api/projects/{pid}/accuracy-report").json()["screening"]["title_abstract"]

    assert body["n"] == 5 and body["skipped_human_maybe"] == 1  # the undecided source is not counted at all
    assert (body["recall"], body["recall_not_excluded"], body["precision"], body["false_exclusions"]) == (0.333, 0.667, 0.5, 1)
    assert client.get(f"/api/projects/{pid}/accuracy-report").json()["screening"]["full_text"]["n"] == 0


def _extraction(pid, source_id, *, by, verified, fields, ai_fields, days=0):
    when = datetime(2026, 10, 1 + days, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(Extraction(project_id=uuid.UUID(pid), source_id=uuid.UUID(source_id), schema_version="default@1", fields_json=fields,
                          ai_fields_json=ai_fields, evidence_spans={}, verified_by_human=verified, verified_by="tester" if verified else None,
                          verified_at=when if verified else None, extracted_by=by, extractor="agent:test" if by == "ai" else "tester",
                          created_at=when, updated_at=when))
        db.commit()


def test_the_extraction_report_compares_the_ais_first_values_with_what_a_person_verified():
    client, pid = _world("ext")
    edited = {"sample": {"n": 100, "population": "clerks", "country": "NL"}, "key_findings": "Output rose", "measures": ["scale"]}
    first = {"sample": {"n": 100, "population": "clerks", "country": "NL"}, "key_findings": "Output fell", "measures": None}
    _extraction(pid, _source(client, pid, "Scored"), by="ai", verified=True, fields=edited, ai_fields=first)
    _extraction(pid, _source(client, pid, "Unverified"), by="ai", verified=False, fields=first, ai_fields=first)
    _extraction(pid, _source(client, pid, "No original"), by="ai", verified=True, fields=edited, ai_fields=None)
    _extraction(pid, _source(client, pid, "By a person"), by="human", verified=True, fields=edited, ai_fields=None)

    body = client.get(f"/api/projects/{pid}/accuracy-report").json()["extraction"]

    assert (body["ai_extractions"], body["not_verified_yet"], body["no_ai_original_kept"]) == (3, 1, 1)
    fields = body["by_schema"]["default@1"]["fields"]
    assert body["by_schema"]["default@1"]["papers"] == 1
    assert (fields["sample"]["precision"], fields["sample"]["recall"], fields["sample"]["critical"]) == (1.0, 1.0, True)
    assert (fields["key_findings"]["precision"], fields["key_findings"]["recall"]) == (0.0, 0.0)  # the person changed it
    assert (fields["measures"]["precision"], fields["measures"]["recall"]) == (None, 0.0)  # the AI abstained, the person filled it


def test_a_persons_edit_changes_the_final_values_but_not_the_ais_first_values():
    client, pid = _world("keep")
    first = {"sample": {"n": 1, "population": "x", "country": "y"}}
    _extraction(pid, _source(client, pid, "Edited later"), by="ai", verified=False, fields=first, ai_fields=first)
    row_id = client.get(f"/api/projects/{pid}/extractions").json()[0]["id"]

    assert client.put(f"/api/projects/{pid}/extractions/{row_id}", json={"fields": {"sample": None}, "evidence": {}}).status_code == 200

    with SessionLocal() as db:
        row = db.get(Extraction, uuid.UUID(row_id))
        assert row.fields_json == {"sample": None} and row.ai_fields_json == first
