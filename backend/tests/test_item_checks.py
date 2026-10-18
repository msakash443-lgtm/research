import uuid

import pytest
from fastapi.testclient import TestClient

from app.item_checks import check_item
from app.main import app


def _kinds(text: str) -> list[str]:
    return [f.kind for f in check_item(text).flags]


@pytest.mark.parametrize("text", [
    "Don't you agree that the course was useful?",
    "Isn't it true that the new process is faster?",
    "Wouldn't you say the manager is fair?",
    "Most people agree the reform worked.",
    "As we all know, remote work is productive.",
])
def test_leading_wording_is_flagged(text):
    assert "leading" in _kinds(text)


@pytest.mark.parametrize("text", [
    "Don’t you agree that the course was useful?",  # right single quotation mark, as word processors insert
    "Isn‘t it true that the new process is faster?",  # left single quotation mark
    "Wouldnʼt you say the manager is fair?",  # modifier letter apostrophe
])
def test_curly_apostrophes_do_not_hide_leading_wording(text):
    # review defect (M4.4.1): pasted items use curly quotes, which the straight-quote pattern never matched
    assert "leading" in _kinds(text)


@pytest.mark.parametrize("text", [
    "Everyone knows the policy works.",  # codex nit (M4.4.1): the presupposition patterns were untested
    "It is clear that the plan works.",
    "How much do you love this product?",  # the assumed-strong-view form
])
def test_each_leading_pattern_is_pinned_by_a_test(text):
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
