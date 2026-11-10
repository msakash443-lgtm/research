import uuid

import pytest
from fastapi.testclient import TestClient

from app.item_checks import check_item
from app.main import app


def _kinds(text: str) -> list[str]:
    return [f.kind for f in check_item(text).flags]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Don't you agree that the course was useful?", id="dont-you-agree"),
        pytest.param("Isn't it true that the new process is faster?", id="isnt-it-true"),
        pytest.param("Wouldn't you say the manager is fair?", id="wouldnt-you-say"),
        pytest.param("Most people agree the reform worked.", id="most-people-agree"),
        pytest.param("As we all know, remote work is productive.", id="as-we-all-know"),
        # curly quotes (M4.4.1 review defect): pasted items use curly quotes, which the
        # straight-quote pattern never matched.
        pytest.param(
            "Don’t you agree that the course was useful?",
            id="curly-apostrophe-right-single-quotation-mark",
        ),
        pytest.param(
            "Isn‘t it true that the new process is faster?",
            id="curly-apostrophe-left-single-quotation-mark",
        ),
        pytest.param(
            "Wouldnʼt you say the manager is fair?", id="curly-apostrophe-modifier-letter"
        ),
        # presupposition patterns (M4.4.1 codex nit): previously untested.
        pytest.param("Everyone knows the policy works.", id="everyone-knows"),
        pytest.param("It is clear that the plan works.", id="it-is-clear-that"),
        pytest.param("How much do you love this product?", id="assumed-strong-view-how-much"),
    ],
)
def test_leading_wording_is_flagged(text):
    assert "leading" in _kinds(text)


@pytest.mark.parametrize("text", [
    "How well do you sleep?",  # codex nit (M4.4.1): neutral wording must not be flagged as leading
    "How good do you feel about your job?",
])
def test_neutral_how_do_you_questions_are_not_flagged_as_leading(text):
    assert "leading" not in _kinds(text)


def test_or_is_flagged_as_possibly_double_barrelled():
    assert "double_barrelled" in _kinds("Do you prefer tea or coffee?")


def test_an_item_at_the_length_limit_is_accepted_and_one_past_it_is_refused():
    # codex nit (M4.4.1): the 1000-character boundary was untested
    assert check_item("a" * 1000).text == "a" * 1000
    with pytest.raises(ValueError):
        check_item("a" * 1001)


def test_double_barrelled_items_are_flagged_with_the_joining_word():
    result = check_item("The course was interesting and useful.")
    assert "double_barrelled" in [f.kind for f in result.flags]
    assert any(f.matched == "and" for f in result.flags if f.kind == "double_barrelled")


def test_a_neutral_item_has_no_flags():
    assert check_item("The course met my expectations.").flags == []


def test_every_flag_says_it_is_a_heuristic_for_the_researcher_to_judge():
    for f in check_item("Don't you agree the course was interesting and useful?").flags:
        assert "researcher" in f.reason.lower()


def test_blank_items_are_refused():
    with pytest.raises(ValueError):
        check_item("   ")


def test_api_checks_a_list_of_items_without_storing_anything():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ic-{uuid.uuid4().hex}@example.com", "display_name": "I"})
    pid = client.post("/api/projects", json={"title": "Items"}).json()["id"]
    r = client.post(
        f"/api/projects/{pid}/instrument/item-checks",
        json={"items": ["Don't you agree the course was useful?", "The course met my expectations."]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is False
    assert [i["index"] for i in body["items"]] == [0, 1]
    assert body["items"][0]["flags"] and body["items"][1]["flags"] == []


def test_api_refuses_an_empty_list_and_too_many_items():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ic2-{uuid.uuid4().hex}@example.com", "display_name": "I"})
    pid = client.post("/api/projects", json={"title": "Items"}).json()["id"]
    url = f"/api/projects/{pid}/instrument/item-checks"
    assert client.post(url, json={"items": []}).status_code == 422
    assert client.post(url, json={"items": ["x"] * 201}).status_code == 422
