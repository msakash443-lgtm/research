import uuid
from datetime import datetime, timezone

from app.contradictions import detect
from app.database import SessionLocal
from app.models import Extraction
from tests.test_evidence_matrix import _world


def _paper(source_id, statement, quote, *, checked=True):
    return {
        "source_id": source_id, "title": f"Study {source_id}", "extraction_id": source_id,
        "verified_by_human": checked,
        "cells": {"key_findings": {"status": "checked" if checked else "matched",
                                 "value": [{"statement": statement}], "quote": quote,
                                 "page": 2, "section": None,
                                 "source_text_url": f"/evidence/{source_id}"}},
    }


def test_opposing_verified_findings_include_both_traceable_citations():
    papers = [_paper("a", "Training increases retention", "Retention rose"),
              _paper("b", "Training decreases retention", "Retention fell")]
    flags = detect({"papers": papers})
    assert len(flags) == 1
    assert [side["source_id"] for side in flags[0]["citations"]] == ["a", "b"]
    assert [side["quote"] for side in flags[0]["citations"]] == ["Retention rose", "Retention fell"]
    assert [side["source_text_url"] for side in flags[0]["citations"]] == ["/evidence/a", "/evidence/b"]
    assert all(side["quote_scope"] == "key_findings_cell" for side in flags[0]["citations"])


def test_different_outcomes_unverified_or_unsupported_wording_do_not_flag():
    papers = [_paper("a", "Training increases retention", "Rise"),
              _paper("b", "Training decreases wages", "Fall"),
              _paper("c", "Training decreases retention", "Fall", checked=False),
              _paper("d", "Training may decrease retention", "Maybe")]
    assert detect({"papers": papers}) == []


def test_each_finding_in_a_multi_item_cell_is_compared_without_claiming_item_level_quote_support():
    first = _paper("a", "Training increases retention", "Cell-level source passage")
    first["cells"]["key_findings"]["value"].append({"statement": "Training increases wages"})
    second = _paper("b", "Training decreases wages", "Other passage")
    flags = detect({"papers": [first, second]})
    assert len(flags) == 1
    assert flags[0]["outcome"] == "wages"
    assert flags[0]["citations"][0]["statement"] == "Training increases wages"
    assert flags[0]["citations"][0]["quote_scope"] == "key_findings_cell"


def test_contradictions_route_is_project_scoped_and_empty_without_verified_findings():
    client, project, _ = _world()
    response = client.get(f"/api/projects/{project}/evidence-matrix/contradictions")
    assert response.status_code == 200
    assert response.json() == {"schema_version": "default@1", "flags": []}


def test_contradictions_route_returns_both_project_citations_for_opposing_findings():
    client, project, first_source = _world()
    second_source = client.post(f"/api/projects/{project}/sources", json={"title": "Second paper"}).json()["id"]
    for source_id, statement, quote in (
        (first_source, "Training increases retention", "Retention rose"),
        (second_source, "Training decreases retention", "Retention fell"),
    ):
        with SessionLocal() as db:
            db.add(Extraction(
                id=uuid.uuid4(), project_id=uuid.UUID(project), source_id=uuid.UUID(source_id),
                schema_version="default@1", fields_json={"key_findings": [{"statement": statement}]},
                evidence_spans={"key_findings": {"quote": quote, "page": 2}},
                verified_by_human=True, verified_by="tester", verified_at=datetime.now(timezone.utc),
                extracted_by="human", extractor="tester",
            ))
            db.commit()
    response = client.get(f"/api/projects/{project}/evidence-matrix/contradictions")
    assert response.status_code == 200
    flags = response.json()["flags"]
    assert len(flags) == 1
    citations = flags[0]["citations"]
    assert {side["source_id"] for side in citations} == {first_source, second_source}
    assert {side["quote"] for side in citations} == {"Retention rose", "Retention fell"}
    assert all(side["source_text_url"].endswith("/fields/key_findings/source-text") for side in citations)
