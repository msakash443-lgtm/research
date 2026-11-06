import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app import datasets as ds
from app.database import SessionLocal
from app.main import app
from app.models import DatasetVersion

RAW = "id,score,note\n1, 4 ,ok\n2,5,\n,,\n"


# ---- pure module ----------------------------------------------------------------------

def test_parse_returns_columns_and_rows_and_the_exact_text():
    text, columns, rows = ds.parse(RAW.encode("utf-8"))
    assert text == RAW
    assert columns == ["id", "score", "note"]
    assert rows[0] == ["1", " 4 ", "ok"]
    assert len(rows) == 3


def test_utf8_bom_is_not_part_of_the_first_column_name():
    # Excel and Google Forms exports often start with a byte-order mark (M5.1 review defect 1).
    text, columns, rows = ds.parse(b"\xef\xbb\xbfid,score\n1,4\n")
    assert columns == ["id", "score"]
    assert rows == [["1", "4"]]
    assert not text.startswith("﻿")


def test_blank_lines_are_skipped_wherever_they_are():
    # M5.1 review defect 2: a blank line mid-file used to raise "Row N has 0 cells".
    _, columns, rows = ds.parse(b"\nid,score\n1,4\n\n2,5\n\n")
    assert columns == ["id", "score"]
    assert rows == [["1", "4"], ["2", "5"]]


def test_a_row_of_empty_cells_is_kept_for_drop_blank_rows_to_handle():
    # Only a truly empty line is skipped; ",," is a row with blank cells and still has to match the header width.
    _, _, rows = ds.parse(b"id,score\n1,4\n,\n")
    assert rows == [["1", "4"], ["", ""]]


@pytest.mark.parametrize("payload", [
    b"",  # empty
    b"\xff\xfe bad utf8",  # not utf-8
    b"a,b\n1\n",  # ragged row
    b"a,a\n1,2\n",  # duplicate header
    b"a,\n1,2\n",  # blank header name
])
def test_malformed_csv_is_refused(payload):
    with pytest.raises(ValueError):
        ds.parse(payload)


def test_too_many_rows_are_refused():
    body = "a\n" + "1\n" * (ds.MAX_ROWS + 1)
    with pytest.raises(ValueError):
        ds.parse(body.encode("utf-8"))


def test_hash_is_sha256_of_the_stored_text():
    assert ds.sha256_hex(RAW) == hashlib.sha256(RAW.encode("utf-8")).hexdigest()


def test_cleaning_trims_drops_blank_rows_and_columns_and_logs_each_step():
    _, columns, rows = ds.parse(RAW.encode("utf-8"))
    steps = [{"op": "trim_whitespace"}, {"op": "drop_blank_rows"}, {"op": "drop_columns", "columns": ["note"]}]
    new_columns, new_rows, log = ds.apply_cleaning(columns, rows, steps)
    assert new_columns == ["id", "score"]
    assert new_rows == [["1", "4"], ["2", "5"]]
    assert [entry["op"] for entry in log] == ["trim_whitespace", "drop_blank_rows", "drop_columns"]
    assert log[1]["rows_before"] == 3 and log[1]["rows_after"] == 2


@pytest.mark.parametrize("steps", [
    [{"op": "rewrite_everything"}],
    [{"op": "drop_columns", "columns": ["missing"]}],
    [{"op": "drop_columns", "columns": ["id", "score", "note"]}],  # would leave no columns
    [{"op": "drop_columns"}],  # no columns named
])
def test_bad_cleaning_steps_are_refused(steps):
    _, columns, rows = ds.parse(RAW.encode("utf-8"))
    with pytest.raises(ValueError):
        ds.apply_cleaning(columns, rows, steps)


def test_serialise_round_trips_through_parse():
    _, columns, rows = ds.parse(RAW.encode("utf-8"))
    text = ds.serialise(columns, rows)
    assert ds.parse(text.encode("utf-8"))[1:] == (columns, rows)


# ---- API ------------------------------------------------------------------------------

@pytest.fixture
def owner():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ds-{uuid.uuid4().hex}@example.com", "display_name": "D"})
    pid = client.post("/api/projects", json={"title": "Data"}).json()["id"]
    return client, pid


def _ingest(client, pid, text=RAW, name="survey"):
    return client.post(f"/api/projects/{pid}/datasets", json={"name": name, "csv_text": text})


def test_ingest_stores_raw_data_with_its_hash_and_version_one(owner):
    client, pid = owner
    r = _ingest(client, pid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version_no"] == 1 and body["kind"] == "raw" and body["parent_id"] is None
    assert body["sha256"] == ds.sha256_hex(RAW)
    assert body["row_count"] == 3 and body["intact"] is True


def test_raw_content_is_returned_exactly(owner):
    client, pid = owner
    did = _ingest(client, pid).json()["id"]
    assert client.get(f"/api/projects/{pid}/datasets/{did}/content").json()["csv_text"] == RAW


def test_cleaning_makes_a_new_version_and_leaves_the_raw_data_unchanged(owner):
    client, pid = owner
    raw = _ingest(client, pid).json()
    r = client.post(
        f"/api/projects/{pid}/datasets/{raw['id']}/clean",
        json={"name": "cleaned", "steps": [{"op": "trim_whitespace"}, {"op": "drop_blank_rows"}]},
    )
    assert r.status_code == 201, r.text
    cleaned = r.json()
    assert cleaned["version_no"] == 2 and cleaned["kind"] == "cleaned" and cleaned["parent_id"] == raw["id"]
    assert cleaned["cleaning_steps"][1]["op"] == "drop_blank_rows"
    assert cleaned["row_count"] == 2 and cleaned["sha256"] != raw["sha256"]
    assert client.get(f"/api/projects/{pid}/datasets/{raw['id']}/content").json()["csv_text"] == RAW


def test_a_dataset_altered_outside_the_api_is_flagged_and_cannot_be_cleaned(owner):
    client, pid = owner
    did = _ingest(client, pid).json()["id"]
    with SessionLocal() as db:
        db.execute(update(DatasetVersion).where(DatasetVersion.id == uuid.UUID(did)).values(content="a\ntampered\n"))
        db.commit()
    assert client.get(f"/api/projects/{pid}/datasets/{did}").json()["intact"] is False
    r = client.post(f"/api/projects/{pid}/datasets/{did}/clean", json={"name": "x", "steps": [{"op": "trim_whitespace"}]})
    assert r.status_code == 409


def test_there_is_no_edit_or_delete_route_for_data(owner):
    client, pid = owner
    did = _ingest(client, pid).json()["id"]
    assert client.delete(f"/api/projects/{pid}/datasets/{did}").status_code == 405
    assert client.patch(f"/api/projects/{pid}/datasets/{did}", json={"csv_text": "x"}).status_code == 405


def test_bad_csv_and_bad_steps_are_422(owner):
    client, pid = owner
    assert _ingest(client, pid, text="a,b\n1\n").status_code == 422
    did = _ingest(client, pid).json()["id"]
    r = client.post(f"/api/projects/{pid}/datasets/{did}/clean", json={"name": "x", "steps": [{"op": "nope"}]})
    assert r.status_code == 422


def test_list_shows_versions_in_order(owner):
    client, pid = owner
    raw = _ingest(client, pid).json()
    client.post(f"/api/projects/{pid}/datasets/{raw['id']}/clean", json={"name": "c", "steps": [{"op": "trim_whitespace"}]})
    versions = client.get(f"/api/projects/{pid}/datasets").json()
    assert [v["version_no"] for v in versions] == [1, 2]


# ---- M5.6 pseudonymisation -------------------------------------------------------------

KEY = b"test-key-not-a-secret"
PII = "id,email,score\n1,ann@example.com,4\n2,,5\n3,ann@example.com,3\n"


def test_pseudonyms_are_keyed_deterministic_and_hide_the_value():
    a = ds.pseudonym("ann@example.com", KEY)
    assert a == ds.pseudonym("ann@example.com", KEY)
    assert a.startswith("P-") and "ann" not in a
    assert a != ds.pseudonym("ann@example.com", b"another-key")


def test_pseudonymise_replaces_values_keeps_links_and_leaves_blanks_blank():
    _, columns, rows = ds.parse(PII.encode("utf-8"))
    new_columns, new_rows, log = ds.apply_cleaning(
        columns, rows, [{"op": "pseudonymise_columns", "columns": ["email"]}], pseudonym_key=KEY
    )
    assert new_columns == columns
    assert new_rows[0][1] == new_rows[2][1] != "ann@example.com"  # same person, same pseudonym
    assert new_rows[1][1] == ""  # blank stays blank
    assert new_rows[0][2] == "4"  # other columns untouched
    assert "ann" not in repr(log)  # the log describes the step, never the values


def test_pseudonymise_refuses_without_a_key_rather_than_hashing_unkeyed():
    _, columns, rows = ds.parse(PII.encode("utf-8"))
    with pytest.raises(ValueError, match="server key"):
        ds.apply_cleaning(columns, rows, [{"op": "pseudonymise_columns", "columns": ["email"]}])


def test_pseudonymise_refuses_unknown_column_and_missing_names():
    _, columns, rows = ds.parse(PII.encode("utf-8"))
    with pytest.raises(ValueError):
        ds.apply_cleaning(columns, rows, [{"op": "pseudonymise_columns", "columns": ["nope"]}], pseudonym_key=KEY)
    with pytest.raises(ValueError):
        ds.apply_cleaning(columns, rows, [{"op": "pseudonymise_columns"}], pseudonym_key=KEY)


def test_pseudonymise_without_a_server_key_is_503_and_creates_no_version(owner):
    client, pid = owner
    # Without a configured key the API must refuse (503), never silently hash.
    raw = _ingest(client, pid, text=PII).json()
    r = client.post(
        f"/api/projects/{pid}/datasets/{raw['id']}/clean",
        json={"name": "p", "steps": [{"op": "pseudonymise_columns", "columns": ["email"]}]},
    )
    assert r.status_code == 503
    assert client.get(f"/api/projects/{pid}/datasets").json()[0]["version_no"] == 1  # no version created


def test_pseudonymise_through_the_api_when_a_key_is_configured(owner, monkeypatch):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from app.routers import datasets as router_module

    monkeypatch.setattr(router_module, "get_settings", lambda: SimpleNamespace(pseudonym_key=SecretStr("api-test-key")))
    client, pid = owner
    raw = _ingest(client, pid, text=PII).json()
    r = client.post(
        f"/api/projects/{pid}/datasets/{raw['id']}/clean",
        json={"name": "p", "steps": [{"op": "pseudonymise_columns", "columns": ["email"]}]},
    )
    assert r.status_code == 201, r.text
    content = client.get(f"/api/projects/{pid}/datasets/{r.json()['id']}/content").json()["csv_text"]
    assert "ann@example.com" not in content
    assert client.get(f"/api/projects/{pid}/datasets/{raw['id']}/content").json()["csv_text"] == PII
