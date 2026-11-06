"""Per-section AI-use disclosure statements in journal-style wordings (X.78)."""

import json
import uuid

import pytest
from sqlalchemy import select

from app import ai_disclosure_formats as f
from app import suggestion_store
from app.database import SessionLocal
from app.models import AuditEvent, Document, DocumentVersion
from tests.test_provenance_reports_api import _doc, _document, _login, _project, _report, _save, _typed

COVERED = [{"document_id": "d1", "title": "Introduction"}, {"document_id": "d2", "title": "Methods"}, {"document_id": "d3", "title": "Results"}]


def span(doc, chars, origin="typed", model=None, action=None):
    return {"document_id": doc, "chars": chars, "origin": origin, "model_id": model, "action": action}


SPANS = [
    span("d1", 80), span("d1", 20, "ai_edited", "model-a", "clearer_sentence"),
    span("d2", 50), span("d2", 50, "ai_edited", "model-b", "find_evidence"),
    span("d3", 40),
]


def test_each_section_gets_its_own_share_models_and_purposes():
    sections = f.per_section(COVERED, SPANS)

    assert [(s["title"], s["chars"], s["ai_chars"], s["ai_share_pct"]) for s in sections] == [
        ("Introduction", 100, 20, 20.0), ("Methods", 100, 50, 50.0), ("Results", 40, 0, 0.0)]
    assert sections[0]["models"] == ["model-a"] and sections[0]["purposes"] == ["suggest clearer wording"]
    assert sections[2]["models"] == [] and sections[2]["purposes"] == []


def test_only_accepted_ai_origins_count_and_an_unknown_action_is_stated_not_guessed():
    sections = f.per_section(COVERED[:1], [span("d1", 10, "typed", "model-a", "challenge"), span("d1", 5, "pasted"),
                                           span("d1", 10, "ai_suggested", "m", "section_draft"), span("d1", 5, "ai_edited", "m", None)])
    assert sections[0]["ai_chars"] == 15
    assert sections[0]["purposes"] == ["assist with writing (action not recorded)", "assist with writing (recorded action: section_draft)"]


def test_every_style_names_the_sections_with_ai_and_lists_all_of_them():
    sections = f.per_section(COVERED, SPANS)
    for style in f.style_names():
        out = f.render(sections, style)
        assert "Introduction and Methods" in out["statement"] and "Results" not in out["statement"], style
        assert "model-a and model-b" in out["statement"], style
        assert [line["title"] for line in out["sections"]] == ["Introduction", "Methods", "Results"]
        assert out["sections"][2]["text"].startswith("Results:")


def test_with_no_ai_the_statement_says_so_for_every_style_and_never_invents_a_tool():
    sections = f.per_section(COVERED, [span("d1", 10), span("d2", 10), span("d3", 10)])
    for style in f.style_names():
        out = f.render(sections, style)
        assert "model" not in out["statement"].lower() and "Introduction" not in out["statement"], style
        assert all(line["text"].endswith(("none.", "recorded.")) for line in out["sections"]), style


def test_the_text_export_carries_the_basis_and_says_it_is_a_template():
    text = f.to_text(f.render(f.per_section(COVERED, SPANS), "elsevier_style"))
    assert text.startswith("Declaration of generative AI and AI-assisted technologies in the writing process\n")
    assert "Basis: the writing record" in text and "check the target journal's current policy" in text and "ai_disclosure_formats@1" in text


def test_an_unknown_style_is_an_error():
    with pytest.raises(KeyError):
        f.render([], "nature_2030")


# ---- API -----------------------------------------------------------------------------------------------------


def _ai_version(project_id, document_id, user_id, text, number=1):
    """A saved version holding one accepted-AI run tied to a real stored suggestion (the save API itself checks this link)."""
    with SessionLocal() as db:
        suggestion = suggestion_store.create_suggestion(
            db, project_id=uuid.UUID(project_id), target_kind="document", target_id=document_id, content=text,
            model_id="model-a", prompt_version="workspace_clearer_sentence@1", producing_agent="workspace", action="clearer_sentence")
        prov = {"sid": "a1", "origin": "ai_edited", "by": user_id, "sug": str(suggestion.id), "note": None, "paste": None, "src": None}
        content = _doc({"type": "paragraph", "content": [{"type": "text", "text": text, "marks": [{"type": "prov", "attrs": prov}]}]})
        version = DocumentVersion(document_id=uuid.UUID(document_id), project_id=uuid.UUID(project_id), number=number, content_json=content,
                                  content_text=text, content_hash=uuid.uuid4().hex * 2, saved_by=user_id, client_save_id=uuid.uuid4().hex)
        db.add(version)
        db.flush()
        db.get(Document, uuid.UUID(document_id)).current_version_id = version.id
        db.commit()


@pytest.fixture
def world():
    client, user_id = _login(f"disc-{uuid.uuid4().hex[:8]}@example.org")
    project_id = _project(client)
    intro = _document(project_id, user_id, "Introduction", 1)
    methods = _document(project_id, user_id, "Methods", 2)
    assert _save(client, project_id, intro, _doc(_typed("Remote work changed wages.", "s1", user_id)), 0).status_code == 201
    _ai_version(project_id, methods, user_id, "We used panel data.")
    report = _report(client, project_id, scope="manuscript").json()
    return dict(client=client, pid=project_id, user_id=user_id, intro=intro, methods=methods, report=report["id"])


def _get(world, **params):
    return world["client"].get(f"/api/projects/{world['pid']}/provenance-reports/{world['report']}/ai-disclosure", params=params)


def test_the_export_is_per_section_and_names_the_model_only_where_ai_text_is_kept(world):
    body = _get(world, style="elsevier_style", format="json").json()

    assert body["style"] == "elsevier_style" and body["template"] == "ai_disclosure_formats@1"
    assert "model-a" in body["statement"] and "Methods" in body["statement"] and "Introduction" not in body["statement"]
    assert [(s["title"], s["text"].split(":", 1)[1].strip()) for s in body["sections"]] == [
        ("Introduction", "none."), ("Methods", "AI-assisted text, 100.0% of characters (model-a).")]


def test_the_text_download_is_an_attachment_in_the_chosen_style(world):
    response = _get(world, style="acknowledgements")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain")
    assert 'filename="ai-use-disclosure-acknowledgements.txt"' in response.headers["content-disposition"]
    assert response.text.startswith("Acknowledgements: use of AI tools\n") and "model-a" in response.text


def test_an_unknown_style_is_refused_and_the_styles_are_listed(world):
    refused = _get(world, style="nature_2030")
    assert refused.status_code == 422 and "elsevier_style" in refused.json()["detail"]

    styles = world["client"].get(f"/api/projects/{world['pid']}/ai-disclosure-styles").json()
    assert [s["id"] for s in styles] == ["acknowledgements", "elsevier_style", "generic"] and all(s["label"] for s in styles)


def test_a_stale_report_is_refused_because_its_figure_could_understate_ai_use(world):
    saved = _save(world["client"], world["pid"], world["intro"], _doc(_typed("Rewritten.", "s9", world["user_id"])), 1)
    assert saved.status_code == 201

    response = _get(world)

    assert response.status_code == 409 and response.json()["detail"]["code"] == "report_stale"


def test_the_export_is_audited_with_ids_and_counts_only(world):
    _get(world, style="generic")

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "provenance.disclosure_exported",
                                                      AuditEvent.project_id == uuid.UUID(world["pid"]))).all()
    assert [e.payload_json for e in events] == [{"report_id": world["report"], "style": "generic", "format": "txt", "sections": 2,
                                                "template": "ai_disclosure_formats@1"}]
    assert "model-a" not in json.dumps([e.payload_json for e in events])


def test_another_project_cannot_read_the_export(world):
    other, _ = _login(f"other-{uuid.uuid4().hex[:8]}@example.org")
    other_project = _project(other)
    assert other.get(f"/api/projects/{other_project}/provenance-reports/{world['report']}/ai-disclosure").status_code == 404
