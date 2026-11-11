import pytest

from app import design_tools, methodology_advisor

from tests.api_helpers import create_project, sign_in


def test_review_type_uses_review_design_even_for_a_quantitative_goal():
    result = methodology_advisor.recommend("literature_review", "relate", human_participants=False)

    assert result["approach"] == "review"
    assert result["design"]["candidates"][0] == "Systematic review"
    assert {"Publication bias", "Search coverage", "Selective reporting"} <= set(result["design"]["threats_to_validity"])


def test_review_causal_recommendations_do_not_accumulate_primary_study_analyses():
    first = methodology_advisor.recommend(
        "literature_review", "test_causal", human_participants=False, can_randomize=False
    )
    second = methodology_advisor.recommend(
        "literature_review", "test_causal", human_participants=False, can_randomize=False
    )
    ordinary = methodology_advisor.recommend("literature_review", "relate", human_participants=False)

    assert first["analysis"] == second["analysis"] == ordinary["analysis"]
    assert "Difference-in-differences" not in [item["technique"] for item in ordinary["analysis"]]


def test_quantitative_goal_on_qualitative_type_adds_a_mismatch_caution():
    result = methodology_advisor.recommend("quantitative_survey", "explore", human_participants=True)

    assert any("research type is quantitative" in caution for caution in result["cautions"])


def test_missing_sampling_frame_uses_nonprobability_strategies_and_cautions():
    result = methodology_advisor.recommend(
        "quantitative_survey", "describe", human_participants=True, accessible_population=False
    )

    assert [item["strategy"] for item in result["sampling"]] == ["Convenience", "Quota"]
    assert any("sampling frame" in caution.casefold() for caution in result["cautions"])


def test_vulnerable_population_adds_safeguards_and_nonparticipant_project_omits_consent():
    with_participants = methodology_advisor.recommend(
        "qualitative", "explore", human_participants=True, vulnerable_population=True
    )
    without_participants = methodology_advisor.recommend("full_journey", "explore", human_participants=False)

    assert any("Extra safeguards" in item["consideration"] for item in with_participants["ethics"])
    assert any("Informed consent" == item["consideration"] for item in with_participants["ethics"])
    assert not any("Informed consent" == item["consideration"] for item in without_participants["ethics"])


def test_sample_size_tests_are_supported_by_design_tools():
    assert {test for tests in methodology_advisor.SIZE_TESTS.values() for test in tests} <= set(design_tools.TESTS)


def test_secondary_data_ethics_is_limited_to_plans_that_suggest_secondary_data():
    quantitative = methodology_advisor.recommend("quantitative_survey", "describe", human_participants=False)
    mixed = methodology_advisor.recommend("mixed_methods", "explain_with_depth", human_participants=False)

    assert any("Data-use terms" in item["consideration"] for item in quantitative["ethics"])
    assert not any("Data-use terms" in item["consideration"] for item in mixed["ethics"])

def test_nonrandomised_causal_design_suggests_quasi_experimental_analyses_only_when_needed():
    randomised = methodology_advisor.recommend(
        "experimental", "test_causal", human_participants=True, can_randomize=True
    )
    nonrandomised = methodology_advisor.recommend(
        "experimental", "test_causal", human_participants=True, can_randomize=False
    )

    assert [item["technique"] for item in randomised["analysis"]] == ["ANOVA/ANCOVA on randomised groups"]
    assert [item["technique"] for item in nonrandomised["analysis"]][1:] == [
        "Difference-in-differences",
        "Regression discontinuity",
    ]

def test_invalid_research_type_and_goal_are_rejected():
    with pytest.raises(ValueError, match="unknown research type 'unknown'"):
        methodology_advisor.recommend("unknown", "explore", human_participants=False)
    with pytest.raises(ValueError, match="goal must be one of"):
        methodology_advisor.recommend("full_journey", "bogus", human_participants=False)


def test_methodology_recommendation_route_rejects_invalid_goal():
    client = sign_in("methodology-advisor-route@example.com")
    project_id = create_project(client, "Methodology advisor route")

    response = client.get(
        f"/api/projects/{project_id}/design/methodology-recommendation", params={"goal": "bogus"}
    )

    assert response.status_code == 422
