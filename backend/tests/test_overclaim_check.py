"""Overclaiming check (M6.7): causal wording under a non-experimental design and claims that reach past any sample.
The pure scan is tested on text; the route is tested on a saved section. Findings are advisory and change nothing."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.overclaim_check import check_text


def _kinds(text, design="observational"):
    return [f.kind for f in check_text(text, design).findings]


def test_causal_wording_is_flagged_for_an_observational_design():
    report = check_text("Remote work causes burnout among staff.")
    assert [f.kind for f in report.findings] == ["causal_language"]
    assert "causes" in report.findings[0].message and report.findings[0].subject.startswith("Remote work causes")


def test_the_same_sentence_is_fine_for_an_experimental_design():
    assert _kinds("Remote work causes burnout among staff.", "experimental") == []


def test_unspecified_design_still_checks_causal_wording():
    assert _kinds("Training leads to higher pay.", "unspecified") == ["causal_language"]


def test_hedged_or_associational_wording_is_not_flagged():
    assert _kinds("Remote work may lead to burnout.") == []
    assert _kinds("Remote work is associated with burnout.") == []


def test_generalization_is_flagged_even_for_an_experiment():
    assert _kinds("All adults always prefer a shorter commute.", "experimental") == ["overgeneralization"]


def test_a_sentence_can_carry_both_findings_and_each_sentence_is_judged_alone():
    text = "The programme improves outcomes for everyone. Attendance was recorded weekly."
    report = check_text(text)
    assert [f.kind for f in report.findings] == ["causal_language", "overgeneralization"]
    assert all(f.subject.startswith("The programme") for f in report.findings)


def test_clean_text_and_empty_text_report_ok():
    assert check_text("Participants completed two surveys.").ok
    assert check_text("").ok


def test_past_tense_change_verbs_are_flagged_like_the_present_tense():
    for sentence in ("Exercise reduced stress.", "The intervention lowered blood pressure.",
                     "Training increased pay.", "Mentoring improved retention.", "The policy raised wages.",
                     "Subsidies boosted uptake.", "The tax decreased smoking."):
        assert _kinds(sentence) == ["causal_language"], sentence

def test_adjectival_lower_is_not_flagged_as_a_change_verb():
    assert _kinds("Participants from lower income groups were recruited.") == []
    assert _kinds("The lower bound of the interval is 0.2.") == []


def test_a_past_tense_change_verb_followed_by_with_is_not_flagged():
    assert _kinds("Blood pressure increased with age.") == []


def test_due_to_and_because_of_are_flagged():
    assert _kinds("Turnover rose due to long hours.") == ["causal_language"]
    assert _kinds("Turnover rose because of long hours.") == ["causal_language"]


def test_an_unspecified_design_says_the_design_is_not_known_instead_of_naming_it():
    message = check_text("Training leads to higher pay.", "unspecified").findings[0].message
    assert "unspecified design" not in message and "a unspecified" not in message
    assert "design is not stated" in message


def test_a_long_sentence_is_cut_to_a_160_character_excerpt():
    sentence = "Remote work causes burnout " + "among staff in many offices " * 10 + "."
    subject = check_text(sentence).findings[0].subject
    assert len(sentence) > 160 and len(subject) <= 160 and subject.endswith("…") and sentence.startswith(subject[:-1])
    short = "Remote work causes burnout."
    assert check_text(short).findings[0].subject == short


def test_an_unknown_design_is_refused_rather_than_switching_the_check_off():
    with pytest.raises(ValueError):
        check_text("Anything.", "experimentl")


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "O"})
    return client


def _section_with(client, text):
    pid = client.post("/api/projects", json={"title": "Overclaim"}).json()["id"]
    did = client.post(f"/api/projects/{pid}/documents", json={"title": "Discussion", "position": 0}).json()["id"]
    user_id = client.get("/api/auth/me").json()["id"]
    prov = {"sid": "a" * 8, "origin": "typed", "by": user_id, "sug": None, "note": None, "paste": None, "src": None}
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": text, "marks": [{"type": "prov", "attrs": prov}]}]}]}
    response = client.post(f"/api/projects/{pid}/documents/{did}/versions", json={
        "base_number": 0, "client_save_id": uuid.uuid4().hex, "client_session_id": "s", "content_json": content})
    assert response.status_code == 201, response.text
    return pid, did


def test_the_route_reports_findings_for_the_latest_saved_version():
    client = _login("overclaim.route@example.org")
    pid, did = _section_with(client, "Remote work causes burnout. Everyone agrees.")
    body = client.get(f"/api/projects/{pid}/documents/{did}/overclaim-check").json()
    assert body["design"] == "observational" and body["ok"] is False and body["version_id"]
    assert [f["kind"] for f in body["findings"]] == ["causal_language", "overgeneralization"]


def test_the_route_honours_the_design_query_and_refuses_an_unknown_one():
    client = _login("overclaim.design@example.org")
    pid, did = _section_with(client, "Remote work causes burnout.")
    url = f"/api/projects/{pid}/documents/{did}/overclaim-check"
    assert client.get(url, params={"design": "experimental"}).json()["ok"] is True
    assert client.get(url, params={"design": "bogus"}).status_code == 422


def test_an_unsaved_section_is_ok_with_no_version():
    client = _login("overclaim.empty@example.org")
    pid = client.post("/api/projects", json={"title": "Empty"}).json()["id"]
    did = client.post(f"/api/projects/{pid}/documents", json={"title": "Intro", "position": 0}).json()["id"]
    body = client.get(f"/api/projects/{pid}/documents/{did}/overclaim-check").json()
    assert body["ok"] is True and body["version_id"] is None


def test_another_projects_section_is_not_found():
    client = _login("overclaim.scope@example.org")
    mine = client.post("/api/projects", json={"title": "Mine"}).json()["id"]
    _, foreign = _section_with(_login("overclaim.other@example.org"), "Text.")
    assert client.get(f"/api/projects/{mine}/documents/{foreign}/overclaim-check").status_code == 404
