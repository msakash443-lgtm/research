import uuid

import pytest
from fastapi.testclient import TestClient

from app import saturation_guidance as sg
from app.main import app


def test_interview_ranges_match_the_cited_studies():
    result = sg.saturation_guidance("interviews")
    by_measure = {r["measure"]: r for r in result["ranges"]}
    code = by_measure["Code saturation (range of themes identified)"]
    assert (code["low"], code["high"], code["unit"]) == (9, 9, "interviews")
    meaning = by_measure["Meaning saturation (fuller understanding)"]
    assert (meaning["low"], meaning["high"]) == (16, 24)
    empirical = by_measure["Empirical studies overall"]
    assert (empirical["low"], empirical["high"]) == (9, 17)
    assert {"hennink_kaiser_marconi_2017", "hennink_kaiser_2022"} <= {s for r in result["ranges"] for s in r["sources"]}


def test_focus_group_range_matches_the_systematic_review():
    result = sg.saturation_guidance("focus_groups")
    assert [(r["low"], r["high"], r["unit"]) for r in result["ranges"]] == [(4, 8, "focus groups")]
    assert result["ranges"][0]["sources"] == ["hennink_kaiser_2022"]


def test_every_cited_source_is_in_the_reference_list():
    for design in sg.DESIGNS:
        for r in sg.saturation_guidance(design)["ranges"]:
            assert r["sources"], r["measure"]
            assert set(r["sources"]) <= set(sg.SOURCES)


def test_result_always_says_saturation_is_judged_in_analysis():
    for design in sg.DESIGNS:
        result = sg.saturation_guidance(design)
        assert "during analysis" in result["caveat"]
        assert result["ranges"]


def test_heterogeneous_or_broad_studies_are_told_to_plan_for_the_upper_end():
    assert any("upper end" in c for c in sg.saturation_guidance("interviews", homogeneous=False)["cautions"])
    assert any("upper end" in c for c in sg.saturation_guidance("interviews", narrow_objective=False)["cautions"])
    assert not sg.saturation_guidance("interviews", homogeneous=True, narrow_objective=True)["cautions"]


def test_unknown_design_and_designs_without_a_verified_source_are_refused():
    with pytest.raises(ValueError):
        sg.saturation_guidance("vibes")
    # Ethnography, grounded theory etc. have no verified source yet, so they must not get a number.
    with pytest.raises(ValueError):
        sg.saturation_guidance("ethnography")


@pytest.fixture
def owner_project():
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"sg-{uuid.uuid4().hex[:8]}@example.com", "display_name": "S"})
    return owner, owner.post("/api/projects", json={"title": "Saturation"}).json()["id"]


def test_api_returns_guidance(owner_project):
    owner, pid = owner_project
    r = owner.get(f"/api/projects/{pid}/design/saturation-guidance", params={"design": "interviews", "homogeneous": False})
    assert r.status_code == 200
    body = r.json()
    assert body["design"] == "interviews"
    assert any("upper end" in c for c in body["cautions"])
    assert "sources" in body and "hennink_kaiser_2022" in body["sources"]


def test_api_rejects_bad_design(owner_project):
    owner, pid = owner_project
    base = f"/api/projects/{pid}/design/saturation-guidance"
    assert owner.get(base, params={"design": "vibes"}).status_code == 422
    assert owner.get(base).status_code == 422  # design is required
