import uuid

import pytest
from fastapi.testclient import TestClient

from app import transcripts
from app.main import app

TEXT = "Anna met Ben. ben said hi. Annabel was not there. Anna's notes say Ben left."


def test_listed_terms_are_replaced_whole_word_and_case_insensitively():
    out, log = transcripts.redact(TEXT, ["Anna", "Ben"])
    assert out == "[REDACTED-1] met [REDACTED-2]. [REDACTED-2] said hi. Annabel was not there. [REDACTED-1]'s notes say [REDACTED-2] left."
    assert log == [{"term_index": 1, "count": 2}, {"term_index": 2, "count": 3}]


def test_the_same_term_in_different_case_shares_one_placeholder():
    out, log = transcripts.redact("Anna and anna", ["Anna", "ANNA"])
    assert out == "[REDACTED-1] and [REDACTED-1]"
    assert log == [{"term_index": 1, "count": 2}]


def test_longer_terms_win_over_shorter_ones_they_contain():
    out, _ = transcripts.redact("Anna", ["Ann", "Anna"])
    assert out == "[REDACTED-2]"


def test_a_term_with_no_match_is_logged_with_zero_so_the_check_is_visible():
    out, log = transcripts.redact("nothing here", ["Zed"])
    assert out == "nothing here"
    assert log == [{"term_index": 1, "count": 0}]


def test_the_log_never_holds_the_terms_themselves():
    _, log = transcripts.redact(TEXT, ["Anna", "Ben"])
    assert "Anna" not in str(log) and "Ben" not in str(log)


def test_case_folding_characters_do_not_raise_a_key_error():
    out, log = transcripts.redact("ſtreet", ["street"])
    assert out == "[REDACTED-1]"
    assert log == [{"term_index": 1, "count": 1}]


def test_dotted_capital_i_matches_its_own_term_without_error():
    out, log = transcripts.redact("İstanbul istanbul ISTANBUL", ["istanbul"])
    assert out == "[REDACTED-1] [REDACTED-1] [REDACTED-1]"
    assert log == [{"term_index": 1, "count": 3}]


def test_text_that_already_contains_a_placeholder_is_refused():
    with pytest.raises(ValueError):
        transcripts.redact("already [REDACTED-1] here, Anna too", ["Anna"])


@pytest.mark.parametrize("terms", [[], ["  "], [""]])
def test_empty_or_blank_term_lists_are_refused(terms):
    with pytest.raises(ValueError):
        transcripts.redact(TEXT, terms)


def test_anonymise_route_returns_text_and_log_and_stores_nothing():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"tr-{uuid.uuid4().hex}@example.com", "display_name": "T"})
    pid = client.post("/api/projects", json={"title": "Transcripts"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/transcripts/anonymise", json={"text": TEXT, "terms": ["Ben"]})
    assert r.status_code == 200, r.text
    assert "Ben" not in r.json()["text"]
    assert r.json()["log"] == [{"term_index": 1, "count": 3}]
    assert client.post(f"/api/projects/{pid}/transcripts/anonymise", json={"text": TEXT, "terms": []}).status_code == 422
