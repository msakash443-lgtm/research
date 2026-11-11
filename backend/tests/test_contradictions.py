import uuid
from datetime import datetime, timezone

from app.contradictions import detect
from app.database import SessionLocal
from app.models import Extraction
from tests.test_evidence_matrix import _world


def _paper(source_id, findings, quote="Evidence passage", *, checked=True):
    return {
        "source_id": source_id, "title": f"Study {source_id}", "extraction_id": source_id,
        "verified_by_human": checked,
        "cells": {"key_findings": {"status": "checked" if checked else "matched",
                                 "value": findings, "quote": quote,
                                 "page": 2, "section": None,
                                 "source_text_url": f"/evidence/{source_id}"}},
    }


def _finding(exposure, outcome, direction, population=""):
    return {"statement": f"{exposure} and {outcome}", "exposure": exposure, "outcome": outcome,
            "population": population, "direction": direction}


def test_opposing_directions_on_the_same_exposure_outcome_are_flagged_as_a_conflict():
    papers = [_paper("a", [_finding("Training", "Retention", "positive")], "Retention rose"),
              _paper("b", [_finding("Training", "Retention", "negative")], "Retention fell")]
    result = detect({"papers": papers})
    assert result["inconsistent"] == [] and result["agreements"] == [] and result["unstructured_findings"] == 0
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert (conflict["exposure"], conflict["outcome"]) == ("Training", "Retention")
    assert "population_note" not in conflict
    assert {finding["source_id"] for finding in conflict["findings"]} == {"a", "b"}
    assert {finding["direction"] for finding in conflict["findings"]} == {"positive", "negative"}
    assert {finding["quote"] for finding in conflict["findings"]} == {"Retention rose", "Retention fell"}


def test_an_effect_against_no_reported_effect_is_flagged_as_inconsistent():
    papers = [_paper("a", [_finding("Training", "Retention", "positive")], "Retention rose"),
              _paper("b", [_finding("Training", "Retention", None)], "No significant effect found")]
    result = detect({"papers": papers})
    assert result["conflicts"] == [] and result["agreements"] == [] and result["unstructured_findings"] == 0
    assert len(result["inconsistent"]) == 1
    assert {finding["direction"] for finding in result["inconsistent"][0]["findings"]} == {"positive", None}


def test_two_or_more_papers_reporting_the_same_direction_are_an_agreement():
    papers = [_paper("a", [_finding("Training", "Retention", "positive")], "Retention rose in A"),
              _paper("b", [_finding("Training", "Retention", "positive")], "Retention rose in B"),
              _paper("c", [_finding("Training", "Wages", "positive")], "Unrelated outcome")]
    result = detect({"papers": papers})
    assert result["conflicts"] == [] and result["inconsistent"] == [] and result["unstructured_findings"] == 0
    assert len(result["agreements"]) == 1
    agreement = result["agreements"][0]
    assert (agreement["exposure"], agreement["outcome"]) == ("Training", "Retention")
    assert {finding["source_id"] for finding in agreement["findings"]} == {"a", "b"}


def test_a_conflict_between_papers_with_differing_populations_is_labelled():
    papers = [_paper("a", [_finding("Training", "Retention", "positive", "Nurses")], "Retention rose"),
              _paper("b", [_finding("Training", "Retention", "negative", "Teachers")], "Retention fell")]
    result = detect({"papers": papers})
    assert result["conflicts"][0]["population_note"] == "may differ by population"


def test_same_normalised_population_does_not_get_a_population_note():
    papers = [_paper("a", [_finding("Training", "Retention", "positive", "College Students")], "Rose"),
              _paper("b", [_finding("Training", "Retention", "negative", "college students!")], "Fell")]
    result = detect({"papers": papers})
    assert "population_note" not in result["conflicts"][0]


def test_key_findings_items_without_the_new_comparison_fields_are_counted_as_unstructured():
    old_schema_finding = {"statement": "Training increases retention", "effect_size": None, "p_or_ci": None}
    papers = [_paper("a", [old_schema_finding], "Retention rose")]
    result = detect({"papers": papers})
    assert result == {"conflicts": [], "inconsistent": [], "agreements": [], "unstructured_findings": 1}



def test_non_string_direction_is_counted_as_unstructured_instead_of_crashing():
    malformed_finding = _finding("Training", "Retention", [])
    papers = [_paper("a", [malformed_finding], "Retention rose")]
    assert detect({"papers": papers}) == {
        "conflicts": [], "inconsistent": [], "agreements": [], "unstructured_findings": 1
    }

def test_mixed_direction_findings_are_structured_but_excluded_from_every_comparison():
    papers = [_paper("a", [_finding("Training", "Retention", "mixed")], "Mixed results in A"),
              _paper("b", [_finding("Training", "Retention", "mixed")], "Mixed results in B")]
    result = detect({"papers": papers})
    assert result == {"conflicts": [], "inconsistent": [], "agreements": [], "unstructured_findings": 0}


def test_unverified_findings_are_not_compared_or_counted_as_unstructured():
    papers = [_paper("a", [_finding("Training", "Retention", "positive")], "Rose", checked=False),
              _paper("b", [_finding("Training", "Retention", "negative")], "Fell")]
    result = detect({"papers": papers})
    assert result == {"conflicts": [], "inconsistent": [], "agreements": [], "unstructured_findings": 0}


def test_different_normalised_exposure_outcome_pairs_are_never_compared():
    papers = [_paper("a", [_finding("Training", "Retention", "positive")], "Rose"),
              _paper("b", [_finding("Training", "Wages", "negative")], "Fell")]
    result = detect({"papers": papers})
    assert result == {"conflicts": [], "inconsistent": [], "agreements": [], "unstructured_findings": 0}


def test_contradictions_route_is_project_scoped_and_empty_without_verified_findings():
    client, project, _ = _world()
    response = client.get(f"/api/projects/{project}/evidence-matrix/contradictions")
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "default@2", "conflicts": [], "inconsistent": [], "agreements": [],
        "unstructured_findings": 0,
    }


def test_contradictions_route_returns_a_conflict_for_opposing_verified_findings_from_different_sources():
    client, project, first_source = _world()
    second_source = client.post(f"/api/projects/{project}/sources", json={"title": "Second paper"}).json()["id"]
    for source_id, direction, quote in (
        (first_source, "positive", "Retention rose"),
        (second_source, "negative", "Retention fell"),
    ):
        with SessionLocal() as db:
            db.add(Extraction(
                id=uuid.uuid4(), project_id=uuid.UUID(project), source_id=uuid.UUID(source_id),
                schema_version="default@2",
                fields_json={"key_findings": [{"statement": "Training and retention", "exposure": "Training",
                                               "outcome": "Retention", "population": "", "direction": direction}]},
                evidence_spans={"key_findings": {"quote": quote, "page": 2}},
                verified_by_human=True, verified_by="tester", verified_at=datetime.now(timezone.utc),
                extracted_by="human", extractor="tester",
            ))
            db.commit()
    response = client.get(f"/api/projects/{project}/evidence-matrix/contradictions")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "default@2"
    assert body["inconsistent"] == [] and body["agreements"] == [] and body["unstructured_findings"] == 0
    assert len(body["conflicts"]) == 1
    findings = body["conflicts"][0]["findings"]
    assert {finding["source_id"] for finding in findings} == {first_source, second_source}
    assert {finding["quote"] for finding in findings} == {"Retention rose", "Retention fell"}
