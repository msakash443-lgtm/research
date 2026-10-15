import uuid

import pytest
from fastapi.testclient import TestClient

from app import design_tools as dt
from app.main import app


def test_two_means_matches_the_textbook_value():
    assert dt.two_means(0.5) == 64  # d=0.5, alpha .05 two-sided, power .8 -> 64 per group
    assert dt.two_means(0.5, sides=1) < dt.two_means(0.5)
    assert dt.two_means(0.5, power=0.9) > dt.two_means(0.5)


def test_other_calculators_match_known_values():
    assert dt.one_mean(0.5) == 34
    assert dt.two_proportions(0.6, 0.4) == 97
    assert dt.correlation(0.3) == 85
    assert dt.correlation(-0.3) == 85
    assert dt.survey_proportion(0.05) == 385
    assert dt.survey_proportion(0.05, population=500) == 218


def test_finite_population_never_exceeds_the_population():
    assert dt.survey_proportion(0.01, population=50) == 50


@pytest.mark.parametrize("call", [
    lambda: dt.two_means(0), lambda: dt.two_means(0.5, alpha=1), lambda: dt.two_means(0.5, power=0),
    lambda: dt.two_means(0.5, sides=3), lambda: dt.two_proportions(0.5, 0.5), lambda: dt.two_proportions(0, 0.5),
    lambda: dt.correlation(0), lambda: dt.correlation(1), lambda: dt.survey_proportion(0),
    lambda: dt.survey_proportion(0.05, population=0), lambda: dt.sample_size("two_means", {"d": 0.5}, attrition=1),
    lambda: dt.sample_size("two_means", {}), lambda: dt.sample_size("nope", {}),
])
def test_bad_inputs_are_refused(call):
    with pytest.raises(ValueError):
        call()


def test_attrition_inflates_the_recruitment_number_not_the_analysable_one():
    result = dt.sample_size("two_means", {"d": 0.5}, attrition=0.2)
    assert result["n_analysable"] == 64
    assert result["n_to_recruit"] == 80
    assert result["total_to_recruit"] == 160
    assert result["unit"] == "per group"


def test_recommender_rules():
    assert dt.recommend_method("explore")["approach"] == "qualitative"
    assert dt.recommend_method("explain_with_depth")["approach"] == "mixed"
    quasi = dt.recommend_method("test_causal", can_randomize=False)
    assert all(d.startswith("Quasi") for d in quasi["candidate_designs"])
    assert quasi["cautions"]
    assert dt.recommend_method("test_causal", can_randomize=None)["cautions"]
    assert len(dt.recommend_method("test_causal", can_randomize=True)["candidate_designs"]) == 2
    assert dt.recommend_method("describe", accessible_population=False)["cautions"]
    with pytest.raises(ValueError):
        dt.recommend_method("vibes")


@pytest.fixture
def owner_project():
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"dt-{uuid.uuid4().hex[:8]}@example.com", "display_name": "D"})
    return owner, owner.post("/api/projects", json={"title": "Design"}).json()["id"]


def test_api_sample_size_and_recommendation(owner_project):
    owner, pid = owner_project
    r = owner.get(f"/api/projects/{pid}/design/sample-size", params={"test": "two_means", "d": 0.5, "attrition": 0.2})
    assert r.status_code == 200 and r.json()["n_to_recruit"] == 80
    r = owner.get(f"/api/projects/{pid}/design/method-recommendation", params={"goal": "relate"})
    assert r.status_code == 200 and r.json()["approach"] == "quantitative"


def test_api_rejects_bad_input(owner_project):
    owner, pid = owner_project
    base = f"/api/projects/{pid}/design/sample-size"
    assert owner.get(base, params={"test": "two_means"}).status_code == 422  # d missing
    assert owner.get(base, params={"test": "two_means", "d": -1}).status_code == 422
    assert owner.get(base, params={"test": "correlation", "r": 0.3, "d": 0.5}).status_code == 422  # d unused
    assert owner.get(base, params={"test": "bogus"}).status_code == 422
    assert owner.get(f"/api/projects/{pid}/design/method-recommendation", params={"goal": "vibes"}).status_code == 422
