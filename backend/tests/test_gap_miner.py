import uuid

from app.coverage_matrix import Paper
from app.gap_miner import candidates


def _paper(title, source_id, extraction_id, verified, *, future=None, findings=None):
    """One paper in the shape `evidence_matrix.build` produces (only the cells the candidates read)."""
    return {
        "source_id": str(source_id), "title": title, "extraction_id": str(extraction_id),
        "verified_by_human": verified,
        "cells": {
            "future_research": {"value": future, "quote": "q" if future else None},
            "limitations": {"value": None, "quote": None},
            "key_findings": {"value": findings, "quote": "q" if findings else None},
        },
    }


def test_theme_shared_by_two_verified_papers_cites_both_and_prefills_future_research():
    a, b = uuid.uuid4(), uuid.uuid4()
    ea, eb = uuid.uuid4(), uuid.uuid4()
    matrix = {"papers": [
        _paper("A", a, ea, True, future=["More RCTs are needed."]),
        _paper("B", b, eb, True, future=["more  rcts are needed"]),
    ]}
    theme = [c for c in candidates(papers=[], matrix=matrix) if c["kind"] == "theme"]
    assert len(theme) == 1
    assert theme[0]["paper_ids"] == sorted([str(a), str(b)])
    assert sorted(item["source_id"] for item in theme[0]["future_research"]) == sorted([str(a), str(b)])


def test_statements_from_unverified_papers_are_never_suggested():
    a, b = uuid.uuid4(), uuid.uuid4()
    matrix = {"papers": [
        _paper("A", a, uuid.uuid4(), False, future=["More RCTs are needed."]),
        _paper("B", b, uuid.uuid4(), False, future=["More RCTs are needed."]),
    ]}
    assert candidates(papers=[], matrix=matrix) == []


def test_theme_stated_by_one_paper_alone_is_not_returned():
    a = uuid.uuid4()
    matrix = {"papers": [_paper("A", a, uuid.uuid4(), True, future=["More RCTs are needed."])]}
    assert candidates(papers=[], matrix=matrix) == []


def test_opposite_verified_findings_become_a_contradiction_candidate_citing_both_papers():
    a, b = uuid.uuid4(), uuid.uuid4()
    ea, eb = uuid.uuid4(), uuid.uuid4()
    positive = {"exposure": "training", "outcome": "productivity", "population": "adults", "direction": "positive"}
    negative = {"exposure": "training", "outcome": "productivity", "population": "adults", "direction": "negative"}
    matrix = {"papers": [
        _paper("A", a, ea, True, findings=[positive]),
        _paper("B", b, eb, True, findings=[negative]),
    ]}
    found = [c for c in candidates(papers=[], matrix=matrix) if c["kind"] == "contradiction"]
    assert len(found) == 1
    assert found[0]["paper_ids"] == sorted([str(a), str(b)])
    assert found[0]["extraction_ids"] == sorted([str(ea), str(eb)])
    assert "opposite effects" in found[0]["reason"]


def test_cell_held_by_one_paper_is_returned_with_that_paper_and_its_cell():
    a, ea = uuid.uuid4(), uuid.uuid4()
    solo = Paper(source_id=a, title="Solo", extraction_id=ea, verified=True,
                 values={"method": "Survey", "population": "Teachers", "country": "UK"})
    matrix = {"papers": [_paper("Solo", a, ea, True)]}
    thin = [c for c in candidates(papers=[solo], matrix=matrix) if c["kind"] == "thin_cell"]
    assert thin
    assert all(c["paper_ids"] == [str(a)] and len(c["cell_ids"]) == 1 for c in thin)


def test_a_project_without_papers_suggests_nothing():
    assert candidates(papers=[], matrix={"papers": []}) == []
