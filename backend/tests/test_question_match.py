import uuid

import pytest

from app.question_match import rank, score_documents, tokenize
from tests.api_helpers import sign_in_user


def test_tokenize_lowercases_unicode_and_drops_stopwords_and_short_tokens():
    assert tokenize("Remote Work & the Wellbeing of Employees (2021)!") == [
        "remote", "work", "wellbeing", "employees", "2021",
    ]


def test_score_documents_scores_the_document_matching_more_query_terms_higher():
    documents = [
        "a randomised controlled trial of remote work and employee wellbeing outcomes",
        "an editorial opinion piece about office furniture trends",
    ]
    scored = score_documents(documents, ["remote work wellbeing"])
    assert scored[0].score > scored[1].score


def test_score_documents_returns_matched_terms_sorted_deduplicated_lowercase():
    documents = ["Remote Work and remote wellbeing study of remote employees"]
    scored = score_documents(documents, ["Remote", "WELLBEING"])
    assert scored[0].matched_terms == ("remote", "wellbeing")


def test_score_documents_with_no_query_terms_scores_everything_zero_with_no_matches():
    scored = score_documents(["some document text", "another document"], [])
    assert all(s.score == 0.0 and s.matched_terms == () for s in scored)


def test_score_documents_with_no_documents_returns_an_empty_list():
    assert score_documents([], ["remote work"]) == []


def test_rank_keeps_the_same_item_set_and_breaks_ties_by_original_order():
    items = ["c", "a", "b"]
    texts = {
        "a": "remote work wellbeing randomised trial",
        "b": "opinion editorial commentary about furniture",
        "c": "remote work wellbeing randomised trial",
    }
    ranked = rank(items, ["remote work wellbeing"], lambda x: texts[x])
    assert {item for item, _ in ranked} == set(items)
    assert [item for item, _ in ranked][-1] == "b"
    # "a" and "c" score identically (same text): ties keep their original relative order
    order = [item for item, _ in ranked]
    assert order.index("c") < order.index("a")


def test_score_documents_is_deterministic_across_repeated_calls():
    documents = ["remote work and wellbeing study", "an opinion piece", "a randomised trial of remote teams"]
    query_terms = ["remote work wellbeing"]
    first = score_documents(documents, query_terms)
    second = score_documents(documents, query_terms)
    assert [(s.score, s.matched_terms) for s in first] == [(s.score, s.matched_terms) for s in second]


# --- Screening queue (`GET .../screening/queue?sort=question_match`) ---


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = sign_in_user(f"question-match-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Question match"}).json()["id"]
    return {"owner": owner, "owner_id": owner_id, "pid": pid}


def _add_question(client, pid, text, qtype="exploratory"):
    r = client.post(f"/api/projects/{pid}/research-questions", json={"text": text, "type": qtype, "gap_ids": []})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_criteria(client, pid, criteria):
    r = client.put(f"/api/projects/{pid}/criteria", json={"framework": "custom", "criteria": criteria})
    assert r.status_code == 200, r.text


def _add_source(client, pid, title, abstract):
    r = client.post(f"/api/projects/{pid}/sources", json={"title": title, "abstract": abstract})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_question_match_sort_puts_the_matching_record_first_and_lists_its_matched_terms(world):
    owner, pid = world["owner"], world["pid"]
    _add_question(owner, pid, "What is the effect of remote work on employee wellbeing?")
    s_match = _add_source(owner, pid, "Remote work and employee wellbeing", "A randomised study of remote work and wellbeing outcomes among employees.")
    s_other1 = _add_source(owner, pid, "Office furniture trends", "A survey of ergonomic office furniture purchasing trends.")
    s_other2 = _add_source(owner, pid, "History of the postal service", "A historical review of postal service logistics.")

    data = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()

    assert data["sort"] == "question_match"
    assert data["sort_note"] is None
    assert data["items"][0]["source_id"] == s_match
    assert "remote" in data["items"][0]["matched_terms"]
    assert "wellbeing" in data["items"][0]["matched_terms"]
    assert {i["source_id"] for i in data["items"]} == {s_match, s_other1, s_other2}


def test_question_match_sort_falls_back_to_oldest_first_with_no_research_question_or_criteria(world):
    owner, pid = world["owner"], world["pid"]
    ids = [_add_source(owner, pid, f"Paper {n}", f"Abstract {n}") for n in range(3)]

    oldest = owner.get(f"/api/projects/{pid}/screening/queue").json()
    question_match = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()

    assert question_match["sort"] == "oldest"
    assert question_match["sort_note"]
    assert "oldest" in question_match["sort_note"].lower() or "fall" in question_match["sort_note"].lower()
    assert [i["source_id"] for i in question_match["items"]] == [i["source_id"] for i in oldest["items"]] == ids
    assert all(i["matched_terms"] == [] for i in question_match["items"])


def test_question_match_sort_never_excludes_a_record_or_changes_the_total_count(world):
    owner, pid = world["owner"], world["pid"]
    _add_question(owner, pid, "Does intervention X improve outcome Y?")
    ids = {_add_source(owner, pid, f"Paper {n}", f"Some text about topic {n}") for n in range(4)}

    oldest = owner.get(f"/api/projects/{pid}/screening/queue").json()
    question_match = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()

    assert question_match["pending"] == oldest["pending"] == 4
    assert {i["source_id"] for i in question_match["items"]} == {i["source_id"] for i in oldest["items"]} == ids


def test_question_match_sort_uses_inclusion_criteria_terms_too(world):
    owner, pid = world["owner"], world["pid"]
    _set_criteria(owner, pid, [
        {"kind": "include", "text": "Studies of coral reef biodiversity"},
        {"kind": "exclude", "text": "Opinion pieces"},
    ])
    s_match = _add_source(owner, pid, "Coral reef biodiversity survey", "A field survey measuring coral reef biodiversity across sites.")
    s_other = _add_source(owner, pid, "Unrelated economics paper", "An analysis of currency exchange rate volatility.")

    data = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()

    assert data["sort"] == "question_match"
    assert data["items"][0]["source_id"] == s_match
    assert "coral" in data["items"][0]["matched_terms"]


def test_question_match_sort_is_deterministic_across_repeated_requests(world):
    owner, pid = world["owner"], world["pid"]
    _add_question(owner, pid, "What are the effects of sleep deprivation on memory?")
    for n in range(5):
        _add_source(owner, pid, f"Paper {n}", f"Sleep deprivation and memory study number {n}" if n % 2 == 0 else f"Unrelated paper {n}")

    first = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()
    second = owner.get(f"/api/projects/{pid}/screening/queue", params={"sort": "question_match"}).json()

    assert [i["source_id"] for i in first["items"]] == [i["source_id"] for i in second["items"]]
    assert [i["matched_terms"] for i in first["items"]] == [i["matched_terms"] for i in second["items"]]
