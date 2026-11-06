"""Save, restore and ledger API for manuscript sections (X.31.9).

Every save writes a new append-only version and its authorship events; a retry with the same
`client_save_id` returns the existing version; a stale `base_number` is refused with nothing written.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app import ledger
from app.models import AuditEvent, AuthorshipEvent, Document, DocumentVersion, Source


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "D"}).json()["id"]
    return client, user_id


def _project(client, title="Manuscript"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _document(project_id, owner_id):
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title="Introduction", position=1, created_by=f"user:{owner_id}")
        db.add(document)
        db.commit()
        return str(document.id)


def _doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


def _para(*inline):
    return {"type": "paragraph", "content": list(inline)}


def _run(text, sid, by, origin="typed", paste=None, src=None):
    prov = {"sid": sid, "origin": origin, "by": by, "sug": None, "note": None, "paste": paste, "src": src}
    return {"type": "text", "text": text, "marks": [{"type": "prov", "attrs": prov}]}


def _cite(source_id):
    return {"type": "cite", "attrs": {"source_id": source_id}}


def _save(client, project_id, document_id, content, base, client_save_id=None):
    return client.post(
        f"/api/projects/{project_id}/documents/{document_id}/versions",
        json={
            "base_number": base,
            "client_save_id": client_save_id or uuid.uuid4().hex,
            "client_session_id": "session-1",
            "content_json": content,
        },
    )


def _version_count(document_id):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(document_id)))


def _setup(tag):
    owner, owner_id = _login(f"doc-owner-{tag}@example.com")
    project_id = _project(owner)
    return owner, owner_id, project_id, _document(project_id, owner_id)


def test_first_save_writes_version_one_and_an_inserted_event():
    owner, owner_id, project_id, document_id = _setup("first")
    content = _doc(_para(_run("Care work is unpaid.", "s-1", owner_id)))

    response = _save(owner, project_id, document_id, content, base=0)

    assert response.status_code == 201, response.text
    body = response.json()["version"]
    assert body["number"] == 1
    assert body["parent_number"] is None
    assert body["content_text"] == "Care work is unpaid."
    assert body["saved_by"] == owner_id
    with SessionLocal() as db:
        events = db.scalars(select(AuthorshipEvent).where(AuthorshipEvent.document_id == uuid.UUID(document_id))).all()
        assert [(e.kind.value, e.span_id, e.chars_after) for e in events] == [("inserted", "s-1", 20)]
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.saved")) is not None


def test_retrying_the_same_client_save_returns_the_existing_version():
    owner, owner_id, project_id, document_id = _setup("retry")
    content = _doc(_para(_run("Once.", "s-1", owner_id)))

    first = _save(owner, project_id, document_id, content, base=0, client_save_id="save-abc")
    again = _save(owner, project_id, document_id, content, base=0, client_save_id="save-abc")

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["version"]["id"] == first.json()["version"]["id"]
    assert _version_count(document_id) == 1


def test_a_stale_base_number_is_refused_and_writes_nothing():
    owner, owner_id, project_id, document_id = _setup("stale")
    _save(owner, project_id, document_id, _doc(_para(_run("First.", "s-1", owner_id))), base=0)

    response = _save(owner, project_id, document_id, _doc(_para(_run("Second.", "s-2", owner_id))), base=0)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "version_conflict"
    assert detail["current"]["number"] == 1
    assert _version_count(document_id) == 1


def test_a_quotation_without_a_citation_is_refused_and_writes_nothing():
    owner, owner_id, project_id, document_id = _setup("quote")
    quoted = _doc(_para(_run("Borrowed words.", "q-1", owner_id, origin="pasted", paste="quotation")))

    response = _save(owner, project_id, document_id, quoted, base=0)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "quotation_without_citation"
    assert _version_count(document_id) == 0


def test_a_quotation_with_a_citation_to_a_verified_source_is_saved():
    owner, owner_id, project_id, document_id = _setup("cited")
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Sen 2019"}).json()["id"]
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(source_id))
        source.metadata_verified = True
        source.verification_method = "human"
        db.commit()
    content = _doc(_para(_run("Borrowed words.", "q-1", owner_id, origin="pasted", paste="quotation"), _cite(source_id)))

    response = _save(owner, project_id, document_id, content, base=0)

    assert response.status_code == 201, response.text


def test_a_new_citation_to_an_unverified_source_is_refused():
    owner, owner_id, project_id, document_id = _setup("unverified")
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Unchecked"}).json()["id"]
    content = _doc(_para(_run("Claim.", "c-1", owner_id), _cite(source_id)))

    response = _save(owner, project_id, document_id, content, base=0)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "uncited_source"


def test_text_written_under_another_persons_name_is_refused():
    owner, owner_id, project_id, document_id = _setup("foreign")
    content = _doc(_para(_run("Not mine.", "f-1", by="someone-else")))

    response = _save(owner, project_id, document_id, content, base=0)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "foreign_author"
    assert _version_count(document_id) == 0


def test_pasted_text_matching_a_project_source_is_detected_and_returned():
    owner, owner_id, project_id, document_id = _setup("paste")
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Sen 2019"}).json()["id"]
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source_id)).abstract = "Women carry most unpaid care work in most households."
        db.commit()
    content = _doc(_para(_run("women carry most unpaid care work in most households.", "p-1", owner_id, origin="pasted", paste="unconfirmed")))

    response = _save(owner, project_id, document_id, content, base=0)

    assert response.status_code == 201, response.text
    assert response.json()["detected"] == [{"span_id": "p-1", "source_id": source_id, "document_id": None}]
    with SessionLocal() as db:
        event = db.scalar(select(AuthorshipEvent).where(AuthorshipEvent.span_id == "p-1"))
        assert str(event.detected_source_id) == source_id


def test_restore_writes_a_new_version_and_keeps_the_old_ones():
    owner, owner_id, project_id, document_id = _setup("restore")
    first = _save(owner, project_id, document_id, _doc(_para(_run("Version one.", "a", owner_id))), base=0).json()["version"]
    second = _save(owner, project_id, document_id, _doc(_para(_run("Version two.", "b", owner_id))), base=1).json()["version"]

    response = owner.post(f"/api/projects/{project_id}/documents/{document_id}/versions/1/restore", json={"base_number": 2})

    assert response.status_code == 200, response.text
    restored = response.json()["version"]
    assert restored["number"] == 3
    assert restored["restored_from_number"] == 1
    assert restored["content_text"] == "Version one."
    assert owner.get(f"/api/projects/{project_id}/documents/{document_id}/versions/1").json()["content_text"] == first["content_text"]
    assert owner.get(f"/api/projects/{project_id}/documents/{document_id}/versions/2").json()["content_text"] == second["content_text"]
    with SessionLocal() as db:
        kinds = sorted((e.span_id, e.kind.value) for e in db.scalars(
            select(AuthorshipEvent).where(AuthorshipEvent.version_id == uuid.UUID(restored["id"]))))
    assert kinds == [("a", "restored"), ("b", "removed")]


def test_a_restore_with_a_stale_base_is_refused():
    owner, owner_id, project_id, document_id = _setup("restore-stale")
    _save(owner, project_id, document_id, _doc(_para(_run("One.", "a", owner_id))), base=0)
    _save(owner, project_id, document_id, _doc(_para(_run("Two.", "b", owner_id))), base=1)

    response = owner.post(f"/api/projects/{project_id}/documents/{document_id}/versions/1/restore", json={"base_number": 1})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_conflict"
    assert _version_count(document_id) == 2


def test_current_version_list_and_single_version_are_readable():
    owner, owner_id, project_id, document_id = _setup("read")
    _save(owner, project_id, document_id, _doc(_para(_run("One.", "a", owner_id))), base=0)
    _save(owner, project_id, document_id, _doc(_para(_run("Two.", "b", owner_id))), base=1)
    base = f"/api/projects/{project_id}/documents/{document_id}"

    assert owner.get(base).json()["number"] == 2
    assert [v["number"] for v in owner.get(f"{base}/versions").json()] == [1, 2]
    listing = owner.get(f"{base}/versions").json()[0]
    assert "content_json" not in listing
    assert owner.get(f"{base}/versions/1").json()["content_text"] == "One."
    assert owner.get(f"{base}/versions/9").status_code == 404


def test_the_ledger_lists_one_spans_events_and_no_text():
    owner, owner_id, project_id, document_id = _setup("ledger")
    _save(owner, project_id, document_id, _doc(_para(_run("Typed.", "a", owner_id))), base=0)
    _save(owner, project_id, document_id, _doc(_para(_run("Typed again.", "a", owner_id))), base=1)

    events = owner.get(f"/api/projects/{project_id}/documents/{document_id}/ledger", params={"span_id": "a"}).json()

    assert [e["kind"] for e in events] == ["inserted", "changed"]
    assert all("text" not in e and "content_json" not in e for e in events)


def test_a_document_from_another_project_is_not_found():
    owner, owner_id, project_id, document_id = _setup("scope")
    other_project = _project(owner, title="Other")

    response = owner.get(f"/api/projects/{other_project}/documents/{document_id}")

    assert response.status_code == 404


# --- Create, list and patch sections (X.31.20) ---------------------------------------------------


def test_create_a_section_returns_it_and_records_the_author():
    owner, owner_id, project_id, _ = _setup("create")

    response = owner.post(f"/api/projects/{project_id}/documents", json={"title": "Methods", "position": 2})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Methods"
    assert body["position"] == 2
    assert body["created_by"] == owner_id
    assert body["current_version_id"] is None
    assert body["ai_locked"] is False
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.created")) is not None


def test_list_returns_only_this_projects_sections_in_position_order():
    owner, _, project_id, _ = _setup("list")  # seeds "Introduction" at position 1
    other_project = _project(owner, title="Other")
    owner.post(f"/api/projects/{project_id}/documents", json={"title": "Discussion", "position": 3})
    owner.post(f"/api/projects/{project_id}/documents", json={"title": "Methods", "position": 2})
    owner.post(f"/api/projects/{other_project}/documents", json={"title": "Elsewhere", "position": 1})

    listed = owner.get(f"/api/projects/{project_id}/documents")

    assert listed.status_code == 200, listed.text
    assert [row["title"] for row in listed.json()] == ["Introduction", "Methods", "Discussion"]
    assert all("content_json" not in row for row in listed.json())


def test_patch_changes_only_the_fields_sent_and_is_audited():
    owner, _, project_id, document_id = _setup("patch")

    response = owner.patch(f"/api/projects/{project_id}/documents/{document_id}", json={"title": "Background", "ai_locked": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Background"
    assert body["ai_locked"] is True
    assert body["position"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.updated")) is not None


def test_patch_refuses_a_section_from_another_project():
    owner, _, project_id, _ = _setup("cross")
    other_project = _project(owner, title="Other")
    other_document = owner.post(f"/api/projects/{other_project}/documents", json={"title": "Theirs", "position": 1}).json()["id"]

    response = owner.patch(f"/api/projects/{project_id}/documents/{other_document}", json={"title": "Stolen"})

    assert response.status_code == 404


def test_create_and_patch_validate_their_input():
    owner, _, project_id, document_id = _setup("validate")

    assert owner.post(f"/api/projects/{project_id}/documents", json={"title": "   ", "position": 1}).status_code == 422
    assert owner.post(f"/api/projects/{project_id}/documents", json={"title": "x" * 301, "position": 1}).status_code == 422
    assert owner.post(f"/api/projects/{project_id}/documents", json={"title": "Ok", "position": -1}).status_code == 422
    assert owner.post(f"/api/projects/{project_id}/documents", json={"title": "Ok", "position": 1, "content_json": {}}).status_code == 422
    assert owner.patch(f"/api/projects/{project_id}/documents/{document_id}", json={"current_version_id": "x"}).status_code == 422
    assert owner.patch(f"/api/projects/{project_id}/documents/{document_id}", json={}).status_code == 422


def _note(client, body="Unpaid care keeps women out of paid work.\n\nIt also shapes who gets promoted.", quoted_text=None, project_id=None):
    payload = {"client_id": f"c-{uuid.uuid4()}", "kind": "clip" if quoted_text else "typed", "body": body}
    if quoted_text:
        payload["quoted_text"] = quoted_text
    note_id = client.post("/api/notes", json=payload).json()["id"]
    if project_id is not None:
        assert client.post(f"/api/notes/{note_id}/file", json={"project_id": project_id}).status_code == 200
    return note_id


def _from_note(client, project_id, document_id, note_id):
    return client.post(f"/api/projects/{project_id}/documents/{document_id}/paragraph-from-note", json={"note_id": note_id})


def test_paragraph_from_note_returns_a_from_note_fragment_and_saves_nothing():
    owner, owner_id, project_id, document_id = _setup("from-note")
    note_id = _note(owner, project_id=project_id)

    response = _from_note(owner, project_id, document_id, note_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["note_id"] == note_id
    paragraphs = body["content"]
    assert [p["type"] for p in paragraphs] == ["paragraph", "paragraph"]
    assert [p["content"][0]["text"] for p in paragraphs] == ["Unpaid care keeps women out of paid work.", "It also shapes who gets promoted."]
    provs = [p["content"][0]["marks"] for p in paragraphs]
    assert all(marks == [{"type": "prov", "attrs": {
        "sid": body["span_id"], "origin": "from_note", "by": owner_id, "sug": None, "note": note_id, "paste": None, "src": None,
    }}] for marks in provs)
    assert ledger.validate_schema(_doc(*paragraphs)) == []
    assert _version_count(document_id) == 0


def test_a_paragraph_from_a_note_saves_with_a_from_note_ledger_event():
    owner, _, project_id, document_id = _setup("from-note-save")
    note_id = _note(owner, project_id=project_id)
    fragment = _from_note(owner, project_id, document_id, note_id).json()

    response = _save(owner, project_id, document_id, _doc(*fragment["content"]), base=0)

    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        events = db.scalars(select(AuthorshipEvent).where(AuthorshipEvent.document_id == uuid.UUID(document_id))).all()
        assert [(e.span_id, e.origin.value, str(e.note_id)) for e in events] == [(fragment["span_id"], "from_note", note_id)]


def test_each_call_gets_a_new_span_id():
    owner, _, project_id, document_id = _setup("from-note-sid")
    note_id = _note(owner, project_id=project_id)

    first = _from_note(owner, project_id, document_id, note_id).json()["span_id"]
    second = _from_note(owner, project_id, document_id, note_id).json()["span_id"]

    assert first != second


def test_a_note_from_another_user_is_refused_even_when_filed_to_this_project():
    owner, _, project_id, document_id = _setup("from-note-other-user")
    coauthor, _ = _login("doc-coauthor-from-note@example.com")
    invited = owner.post(f"/api/projects/{project_id}/members", json={"email": "doc-coauthor-from-note@example.com", "role": "co_author"})
    assert invited.status_code == 201, invited.text
    their_note = _note(coauthor, project_id=project_id)

    response = _from_note(owner, project_id, document_id, their_note)

    assert response.status_code == 404


def test_a_note_filed_to_another_project_or_not_filed_is_refused():
    owner, _, project_id, document_id = _setup("from-note-other-project")
    other_project = _project(owner, title="Other")
    elsewhere = _note(owner, project_id=other_project)
    unfiled = _note(owner)

    assert _from_note(owner, project_id, document_id, elsewhere).status_code == 404
    assert _from_note(owner, project_id, document_id, unfiled).status_code == 404
    assert _from_note(owner, project_id, document_id, str(uuid.uuid4())).status_code == 404


def test_a_section_from_another_project_is_refused():
    owner, _, project_id, _ = _setup("from-note-cross-doc")
    other_project = _project(owner, title="Other")
    other_document = owner.post(f"/api/projects/{other_project}/documents", json={"title": "Theirs", "position": 1}).json()["id"]
    note_id = _note(owner, project_id=project_id)

    assert _from_note(owner, project_id, other_document, note_id).status_code == 404


def test_quoted_text_is_never_put_in_the_paragraph():
    owner, _, project_id, document_id = _setup("from-note-quote")
    clip_only = _note(owner, body="", quoted_text="Someone else's sentence.", project_id=project_id)
    clip_with_words = _note(owner, body="My reading of it.", quoted_text="Someone else's sentence.", project_id=project_id)

    refused = _from_note(owner, project_id, document_id, clip_only)
    kept = _from_note(owner, project_id, document_id, clip_with_words)

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "note_has_no_text"
    assert kept.status_code == 200
    assert [p["content"][0]["text"] for p in kept.json()["content"]] == ["My reading of it."]


# --- Version-conflict compare view (X.31.16): keep mine / keep theirs / keep both ----------------
# When two browser tabs (or two people) save the same section from the same base version, the loser gets a 409
# `version_conflict` with the server's current version (`routers/documents.py::_conflict`). The editor's compare
# view offers three choices; each resolves through the same `POST .../versions` endpoint, just with a different
# `base_number`/`content_json` (the decision itself is `editor/conflictLogic.js`, unit-tested there).


def test_version_conflict_409_is_resolved_by_keeping_mine():
    owner, owner_id, project_id, document_id = _setup("conflict-mine")
    _save(owner, project_id, document_id, _doc(_para(_run("Saved from tab A.", "s-a", owner_id))), base=0)
    mine = _doc(_para(_run("Written in tab B before it saw tab A's save.", "s-b", owner_id)))

    conflict = _save(owner, project_id, document_id, mine, base=0)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "version_conflict"
    current_number = conflict.json()["detail"]["current"]["number"]

    resolved = _save(owner, project_id, document_id, mine, base=current_number)
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["version"]["content_text"] == "Written in tab B before it saw tab A's save."
    assert _version_count(document_id) == 2


def test_version_conflict_409_is_resolved_by_keeping_theirs():
    owner, owner_id, project_id, document_id = _setup("conflict-theirs")
    _save(owner, project_id, document_id, _doc(_para(_run("Saved from tab A.", "s-a", owner_id))), base=0)
    mine = _doc(_para(_run("Written in tab B before it saw tab A's save.", "s-b", owner_id)))

    conflict = _save(owner, project_id, document_id, mine, base=0)
    assert conflict.status_code == 409
    current = conflict.json()["detail"]["current"]

    # Keeping theirs writes nothing new: the researcher just accepts the version already on the server.
    assert current["content_text"] == "Saved from tab A."
    assert _version_count(document_id) == 1


def test_version_conflict_409_is_resolved_by_keeping_both():
    owner, owner_id, project_id, document_id = _setup("conflict-both")
    _save(owner, project_id, document_id, _doc(_para(_run("Saved from tab A.", "s-a", owner_id))), base=0)
    mine = _doc(_para(_run("Written in tab B before it saw tab A's save.", "s-b", owner_id)))

    conflict = _save(owner, project_id, document_id, mine, base=0)
    current_number = conflict.json()["detail"]["current"]["number"]
    current_text = conflict.json()["detail"]["current"]["content_text"]

    merged = _doc(
        _para(_run("Written in tab B before it saw tab A's save.", "s-merge-1", owner_id)),
        _para(_run("[Server copy]", "s-merge-2", owner_id)),
        _para(_run(current_text, "s-merge-3", owner_id)),
    )
    resolved = _save(owner, project_id, document_id, merged, base=current_number)
    assert resolved.status_code == 201, resolved.text
    merged_text = resolved.json()["version"]["content_text"]
    assert "Written in tab B before it saw tab A's save." in merged_text
    assert "Saved from tab A." in merged_text
    assert _version_count(document_id) == 2
