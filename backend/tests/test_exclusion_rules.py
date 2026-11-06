import uuid

import pytest
from fastapi.testclient import TestClient

from app import exclusion_rules as ex
from app.main import app

COLUMNS = ["id", "score", "group"]
ROWS = [["1", "4", "a"], ["2", "1", "b"], ["3", "", "a"], ["4", "2", "c"]]


# ---- pure module ----------------------------------------------------------------------

def test_a_numeric_rule_is_normalised_and_refuses_a_non_numeric_value():
    rule = ex.normalise({"column": "score", "op": "less_than", "value": "2"}, COLUMNS)
    assert rule == {"column": "score", "op": "less_than", "value": "2"}
    with pytest.raises(ValueError):
        ex.normalise({"column": "score", "op": "less_than", "value": "two"}, COLUMNS)


@pytest.mark.parametrize("rule", [
    {"column": "missing", "op": "equals", "value": "a"},  # unknown column
    {"column": "score", "op": "rewrite", "value": "1"},  # unknown op
    {"column": "group", "op": "equals"},  # value needed
    {"column": "group", "op": "is_blank", "value": "x"},  # value not allowed
])
def test_bad_rules_are_refused(rule):
    with pytest.raises(ValueError):
        ex.normalise(rule, COLUMNS)


def test_rule_hash_does_not_depend_on_key_order():
    a = ex.normalise({"column": "group", "op": "equals", "value": "a"}, COLUMNS)
    b = ex.normalise({"value": "a", "op": "equals", "column": "group"}, COLUMNS)
    assert ex.rule_sha256(a) == ex.rule_sha256(b)
    assert len(ex.rule_sha256(a)) == 64


def test_approved_rules_drop_only_matching_rows_and_log_the_rule():
    rule = ex.normalise({"column": "score", "op": "less_than", "value": "2"}, COLUMNS)
    approved = [{"id": "r1", "rule": rule, "rule_sha256": ex.rule_sha256(rule)}]
    rows, log = ex.apply(COLUMNS, ROWS, approved)
    assert [r[0] for r in rows] == ["1", "3", "4"]  # row 2 (score 1) dropped; row 3 (blank score) kept
    assert log == [{
        "op": "exclude_rows", "rule_id": "r1", "rule_sha256": ex.rule_sha256(rule),
        "rule": rule, "rows_before": 4, "rows_after": 3,
        "detail": "Excluded 1 row(s) matching an approved rule.",
    }]


def test_is_blank_and_equals_work_on_text():
    blank = ex.normalise({"column": "score", "op": "is_blank"}, COLUMNS)
    equals = ex.normalise({"column": "group", "op": "equals", "value": "a"}, COLUMNS)
    approved = [
        {"id": "r1", "rule": blank, "rule_sha256": ex.rule_sha256(blank)},
        {"id": "r2", "rule": equals, "rule_sha256": ex.rule_sha256(equals)},
    ]
    rows, _ = ex.apply(COLUMNS, ROWS, approved)
    assert [r[0] for r in rows] == ["2", "4"]


def test_a_rule_altered_after_approval_is_refused():
    rule = ex.normalise({"column": "group", "op": "equals", "value": "a"}, COLUMNS)
    tampered = dict(rule, value="b")
    with pytest.raises(ValueError):
        ex.apply(COLUMNS, ROWS, [{"id": "r1", "rule": tampered, "rule_sha256": ex.rule_sha256(rule)}])


def test_nothing_is_dropped_without_an_approved_rule():
    rows, log = ex.apply(COLUMNS, ROWS, [])
    assert rows == ROWS and log == []


# ---- API ------------------------------------------------------------------------------

RAW = "id,score,group\n1,4,a\n2,1,b\n3,,a\n4,2,c\n"


@pytest.fixture
def owner():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ex-{uuid.uuid4().hex}@example.com", "display_name": "E"})
    pid = client.post("/api/projects", json={"title": "Exclusions"}).json()["id"]
    did = client.post(f"/api/projects/{pid}/datasets", json={"name": "raw", "csv_text": RAW}).json()["id"]
    return client, pid, did


def _propose(client, pid, did, rule):
    return client.post(f"/api/projects/{pid}/datasets/{did}/exclusion-rules", json=rule)


def test_a_proposed_rule_starts_unapproved_and_changes_no_data(owner):
    client, pid, did = owner
    r = _propose(client, pid, did, {"column": "score", "op": "less_than", "value": "2"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "proposed" and r.json()["approved_by"] is None
    assert client.get(f"/api/projects/{pid}/datasets/{did}").json()["version_no"] == 1


def test_excluding_without_an_approved_rule_is_refused_and_creates_nothing(owner):
    client, pid, did = owner
    rid = _propose(client, pid, did, {"column": "score", "op": "less_than", "value": "2"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/datasets/{did}/exclude", json={"name": "x", "rule_ids": [rid]})
    assert r.status_code == 409, r.text
    assert len(client.get(f"/api/projects/{pid}/datasets").json()) == 1


def test_approve_then_exclude_makes_a_cleaned_version_that_names_the_rule(owner):
    client, pid, did = owner
    rid = _propose(client, pid, did, {"column": "score", "op": "less_than", "value": "2"}).json()["id"]
    a = client.post(f"/api/projects/{pid}/datasets/{did}/exclusion-rules/{rid}/approve")
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "approved" and a.json()["approved_by"]
    assert client.post(f"/api/projects/{pid}/datasets/{did}/exclusion-rules/{rid}/approve").status_code == 200  # idempotent

    r = client.post(f"/api/projects/{pid}/datasets/{did}/exclude", json={"name": "cleaned", "rule_ids": [rid]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "cleaned" and body["parent_id"] == did and body["row_count"] == 3
    assert body["cleaning_steps"][0]["op"] == "exclude_rows" and body["cleaning_steps"][0]["rule_id"] == rid
    assert client.get(f"/api/projects/{pid}/datasets/{did}").json()["row_count"] == 4  # raw untouched


def test_a_rule_from_another_dataset_cannot_be_applied(owner):
    client, pid, did = owner
    other = client.post(f"/api/projects/{pid}/datasets", json={"name": "other", "csv_text": RAW}).json()["id"]
    rid = _propose(client, pid, other, {"column": "score", "op": "is_blank"}).json()["id"]
    client.post(f"/api/projects/{pid}/datasets/{other}/exclusion-rules/{rid}/approve")
    r = client.post(f"/api/projects/{pid}/datasets/{did}/exclude", json={"name": "x", "rule_ids": [rid]})
    assert r.status_code == 404, r.text  # not found for this dataset, the same answer as an unknown id


def test_an_unknown_rule_id_is_404_and_an_empty_list_is_422(owner):
    client, pid, did = owner
    assert client.post(f"/api/projects/{pid}/datasets/{did}/exclusion-rules/{uuid.uuid4()}/approve").status_code == 404
    assert client.post(f"/api/projects/{pid}/datasets/{did}/exclude", json={"name": "x", "rule_ids": []}).status_code == 422


def test_a_bad_rule_is_422_and_nothing_is_stored(owner):
    client, pid, did = owner
    assert _propose(client, pid, did, {"column": "nope", "op": "is_blank"}).status_code == 422
    assert client.get(f"/api/projects/{pid}/datasets/{did}/exclusion-rules").json() == []


def test_the_rule_list_shows_every_proposal_for_the_dataset(owner):
    # Rules proposed in the same second tie on created_at, so the order between them is not promised.
    client, pid, did = owner
    _propose(client, pid, did, {"column": "score", "op": "is_blank"})
    _propose(client, pid, did, {"column": "group", "op": "equals", "value": "c"})
    rules = client.get(f"/api/projects/{pid}/datasets/{did}/exclusion-rules").json()
    assert sorted(r["rule"]["op"] for r in rules) == ["equals", "is_blank"]


def test_the_same_approved_rule_listed_twice_is_refused_not_applied_twice():
    # codex nonblocking nit (M5.7.1): duplicate rule ids were not rejected, so one rule could be logged twice
    rule = ex.normalise({"column": "score", "op": "less_than", "value": "2"}, COLUMNS)
    entry = {"id": "r1", "rule": rule, "rule_sha256": ex.rule_sha256(rule)}
    with pytest.raises(ValueError):
        ex.apply(COLUMNS, ROWS, [entry, dict(entry)])


def test_excluding_the_same_approved_rule_twice_is_422_and_creates_nothing(owner):
    client, pid, did = owner
    rid = _propose(client, pid, did, {"column": "score", "op": "less_than", "value": "2"}).json()["id"]
    client.post(f"/api/projects/{pid}/datasets/{did}/exclusion-rules/{rid}/approve")
    r = client.post(f"/api/projects/{pid}/datasets/{did}/exclude", json={"name": "x", "rule_ids": [rid, rid]})
    assert r.status_code == 422, r.text
    assert len(client.get(f"/api/projects/{pid}/datasets").json()) == 1
