import csv
import io
import pytest

from fastapi.testclient import TestClient

from app.main import app


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


def _project_with_activity():
    owner, owner_id = _login("api-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "=HYPERLINK(\"http://evil\")"}).json()["id"]
    owner.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    owner.post(f"/api/projects/{project_id}/sources", json={"title": "S"})
    return owner, owner_id, project_id


def test_members_can_read_the_trail_and_filter_it():
    owner, owner_id, project_id = _project_with_activity()
    reviewer, _ = _login("api-reviewer@example.com")
    owner.post(f"/api/projects/{project_id}/members", json={"email": "api-reviewer@example.com", "role": "reviewer"})
    url = f"/api/projects/{project_id}/audit"

    everything = reviewer.get(url)
    assert everything.status_code == 200
    assert {e["action"] for e in everything.json()} == {"project.created", "context.added", "source.created", "member.invited"}
    assert [e["action"] for e in owner.get(url, params={"action": "source.created"}).json()] == ["source.created"]
    assert {e["actor"] for e in owner.get(url, params={"actor": owner_id}).json()} == {owner_id}
    assert len(owner.get(url, params={"limit": 2}).json()) == 2
    assert len(owner.get(url, params={"limit": 2, "offset": 3}).json()) == 1
    assert owner.get(url, params={"limit": 0}).status_code == 422
    assert owner.get(url, params={"limit": 5000}).status_code == 422


def test_non_members_cannot_read_or_export():
    _, _, project_id = _project_with_activity()
    stranger, _ = _login("api-stranger@example.com")

    assert stranger.get(f"/api/projects/{project_id}/audit").status_code == 404
    assert stranger.get(f"/api/projects/{project_id}/audit/export").status_code == 404
    assert TestClient(app).get(f"/api/projects/{project_id}/audit").status_code == 401


def test_only_this_projects_events_are_returned():
    owner, _, project_id = _project_with_activity()
    other_id = owner.post("/api/projects", json={"title": "Other"}).json()["id"]

    mine = owner.get(f"/api/projects/{project_id}/audit").json()
    theirs = owner.get(f"/api/projects/{other_id}/audit").json()

    assert len(mine) == 3 and [e["action"] for e in theirs] == ["project.created"]


def test_json_export_is_a_complete_download():
    owner, _, project_id = _project_with_activity()

    response = owner.get(f"/api/projects/{project_id}/audit/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f"audit-{project_id}.json" in response.headers["content-disposition"]
    rows = response.json()
    assert len(rows) == 3 and {"id", "timestamp", "actor", "action", "payload_json"} <= set(rows[0])


def test_csv_export_neutralises_spreadsheet_formulas():
    owner, owner_id, project_id = _project_with_activity()

    response = owner.get(f"/api/projects/{project_id}/audit/export", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["timestamp", "actor", "action", "model_id", "prompt_version", "payload_json", "id"]
    assert len(rows) == 4
    created = next(r for r in rows[1:] if r[2] == "project.created")
    assert created[1] == owner_id
    # The malicious "=HYPERLINK" title sits inside the payload JSON, so no cell starts with a formula
    # character. Direct neutralisation of such cells is covered by test_csv_cell_helper_prefixes_formula_starts.
    assert not any(cell.startswith(("=", "+", "@")) for row in rows for cell in row)


def test_csv_cell_helper_prefixes_formula_starts():
    from app.routers.audit import _csv_cell

    assert _csv_cell("=1+1") == "'=1+1"
    assert _csv_cell("+cmd") == "'+cmd"
    assert _csv_cell("-2") == "'-2"
    assert _csv_cell("@x") == "'@x"
    assert _csv_cell("plain") == "plain"
    assert _csv_cell(None) == ""


def test_export_rejects_unknown_formats():
    owner, _, project_id = _project_with_activity()

    assert owner.get(f"/api/projects/{project_id}/audit/export", params={"format": "xml"}).status_code == 422


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_reads_the_trail_in_batches_not_all_at_once(monkeypatch, fmt):
    from app.routers import audit as audit_router

    owner, _, project_id = _project_with_activity()
    batch_sizes = []
    pulled = [0]
    pulled_at_first_write = []
    real_session_local = audit_router.SessionLocal
    real_model_validate = audit_router.AuditEventRead.model_validate
    real_csv_row = audit_router._csv_row

    class Counting:
        # Counts the rows taken from the database so far, however they are taken.
        def __init__(self, result):
            self._result = result

        def __iter__(self):
            for event in self._result:
                pulled[0] += 1
                yield event

        def all(self):
            events = self._result.all()
            pulled[0] += len(events)
            return events

    def on_write():
        if not pulled_at_first_write:
            pulled_at_first_write.append(pulled[0])

    class RecordingSession:
        def __init__(self, session):
            self._session = session

        def __enter__(self):
            self._session.__enter__()
            return self

        def __exit__(self, *exc):
            return self._session.__exit__(*exc)

        def scalars(self, statement):
            batch_sizes.append(statement._execution_options.get("yield_per"))
            return Counting(self._session.scalars(statement))

    def model_validate(event, *args, **kwargs):
        on_write()
        return real_model_validate(event, *args, **kwargs)

    def csv_row(event):
        on_write()
        return real_csv_row(event)

    monkeypatch.setattr(audit_router, "EXPORT_BATCH_ROWS", 2)
    monkeypatch.setattr(audit_router, "SessionLocal", lambda: RecordingSession(real_session_local()))
    monkeypatch.setattr(audit_router.AuditEventRead, "model_validate", model_validate)
    monkeypatch.setattr(audit_router, "_csv_row", csv_row)

    response = owner.get(f"/api/projects/{project_id}/audit/export", params={"format": fmt})

    assert response.status_code == 200
    rows = response.json() if fmt == "json" else list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 3  # three events, batch size two: the trail spans more than one batch
    assert batch_sizes == [2]  # the rows are fetched through yield_per, never one .all() load
    assert pulled_at_first_write and pulled_at_first_write[0] <= 2  # the body starts before the trail is read
