"""Literature-review summary (X.200): only verified, non-retracted evidence reaches the body."""
import uuid
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Extraction, Source
from tests.test_evidence_matrix import _world


def make_included(client, project, title, *, verified=True, flags=None, direction="positive", extract=True, checked=True):
    sid = client.post(f"/api/projects/{project}/sources", json={"title": title}).json()["id"]
    for stage in ("title_abstract", "full_text"):
        assert client.post(f"/api/projects/{project}/screening/decisions", json={
            "source_id": sid, "stage": stage, "decision": "include"}).status_code == 201
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(sid))
        source.metadata_verified = verified
        source.quality_flags = flags
        if extract:
            now = datetime(2026, 10, 1, tzinfo=timezone.utc)
            finding = {"statement": f"{title}: training changes retention", "exposure": "Training", "outcome": "Retention",
                       "population": "Nurses", "direction": direction}
            db.add(Extraction(project_id=uuid.UUID(project), source_id=source.id, schema_version="default@2",
                              fields_json={"key_findings": [finding]},
                              evidence_spans={"key_findings": {"quote": f"quote of {title}", "page": 3}},
                              verified_by_human=checked, verified_by="tester" if checked else None,
                              verified_at=now if checked else None, extracted_by="human", extractor="tester",
                              created_at=now, updated_at=now))
        db.commit()
    return sid


def summary(client, project, suffix=""):
    response = client.get(f"/api/projects/{project}/literature-summary{suffix}")
    assert response.status_code == 200, response.text
    return response


def test_unverified_retracted_and_unreviewed_sources_are_listed_as_excluded_with_their_reason_and_never_in_the_body():
    client, project, _ = _world()
    good = make_included(client, project, "Good paper")
    unverified = make_included(client, project, "Unverified paper", verified=False)
    retracted = make_included(client, project, "Retracted paper", flags=["retracted"])
    unchecked = make_included(client, project, "Unchecked extraction", checked=False)
    data = summary(client, project).json()

    in_body = {f["source_id"] for theme in data["themes"] for f in theme["findings"]}
    assert in_body == {good}
    reasons = {e["source_id"]: e["reason"] for e in data["excluded"]}
    assert reasons[unverified] == "metadata_unverified" and reasons[retracted] == "retracted"
    assert reasons[unchecked] == "extraction_not_verified" and good not in reasons
    finding = data["themes"][0]["findings"][0]
    assert (finding["quote"], finding["page"]) == ("quote of Good paper", 3)


def test_a_preprint_is_excluded_only_when_the_project_is_peer_reviewed_only():
    client, project, _ = _world()
    preprint = make_included(client, project, "Preprint paper", flags=["preprint"])
    body = lambda: {f["source_id"] for t in summary(client, project).json()["themes"] for f in t["findings"]}
    assert preprint in body()
    assert client.put(f"/api/projects/{project}/screening/peer-reviewed-only", json={"enabled": True}).status_code == 200
    data = summary(client, project).json()
    assert preprint not in body()
    assert {e["source_id"]: e["reason"] for e in data["excluded"]}[preprint] == "preprint_peer_reviewed_only"


def test_conflicts_in_the_summary_use_only_eligible_papers_and_carry_pages():
    client, project, _ = _world()
    make_included(client, project, "Up", direction="positive")
    make_included(client, project, "Down", direction="negative")
    make_included(client, project, "Down but retracted", direction="negative", flags=["retracted"])
    data = summary(client, project).json()
    assert len(data["conflicts"]) == 1
    assert {f["title"] for f in data["conflicts"][0]["findings"]} == {"Up", "Down"}
    assert all(f["page"] == 3 for f in data["conflicts"][0]["findings"])


def test_markdown_format_lists_exclusions_and_cites_source_ids():
    client, project, _ = _world()
    good = make_included(client, project, "Good paper")
    make_included(client, project, "Retracted paper", flags=["retracted"])
    response = summary(client, project, "?format=md")
    assert response.headers["content-type"].startswith("text/markdown")
    assert f"[source {good}, p. 3]" in response.text
    assert "## Sources excluded from this summary" in response.text and "retracted" in response.text.split("## Sources excluded")[1]
    assert "quote of Retracted paper" not in response.text
