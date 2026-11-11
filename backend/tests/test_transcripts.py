import uuid

import pytest
from fastapi.testclient import TestClient

from app import transcripts
from app.main import app

TEXT = "Anna met Ben. ben said hi. Annabel was not there. Anna's notes say Ben left."


@pytest.mark.parametrize(
    "text,terms,expected_out,expected_log",
    [
        pytest.param(
            TEXT,
            ["Anna", "Ben"],
            "[REDACTED-1] met [REDACTED-2]. [REDACTED-2] said hi. Annabel was not there. "
            "[REDACTED-1]'s notes say [REDACTED-2] left.",
            [{"term_index": 1, "count": 2}, {"term_index": 2, "count": 3}],
            id="whole-word-and-case-insensitive",
        ),
        pytest.param(
            "Anna and anna",
            ["Anna", "ANNA"],
            "[REDACTED-1] and [REDACTED-1]",
            [{"term_index": 1, "count": 2}],
            id="the-same-term-in-different-case-shares-one-placeholder",
        ),
        pytest.param(
            "nothing here",
            ["Zed"],
            "nothing here",
            [{"term_index": 1, "count": 0}],
            id="a-term-with-no-match-is-logged-with-zero",
        ),
        pytest.param(
            "ſtreet",
            ["street"],
            "[REDACTED-1]",
            [{"term_index": 1, "count": 1}],
            id="case-folding-characters-do-not-raise-a-key-error",
        ),
        pytest.param(
            "İstanbul istanbul ISTANBUL",
            ["istanbul"],
            "[REDACTED-1] [REDACTED-1] [REDACTED-1]",
            [{"term_index": 1, "count": 3}],
            id="dotted-capital-i-matches-its-own-term-without-error",
        ),
    ],
)
def test_redact_replaces_matching_terms_and_logs_match_counts(text, terms, expected_out, expected_log):
    out, log = transcripts.redact(text, terms)
    assert out == expected_out
    assert log == expected_log


def test_longer_terms_win_over_shorter_ones_they_contain():
    out, _ = transcripts.redact("Anna", ["Ann", "Anna"])
    assert out == "[REDACTED-2]"


def test_the_log_never_holds_the_terms_themselves():
    _, log = transcripts.redact(TEXT, ["Anna", "Ben"])
    assert "Anna" not in str(log) and "Ben" not in str(log)


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
