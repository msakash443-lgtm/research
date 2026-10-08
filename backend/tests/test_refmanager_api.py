from fastapi.testclient import TestClient

from app.main import app


def _project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "refs@example.com", "display_name": "R"})
    project = client.post("/api/projects", json={"title": "Refs", "research_question": "Does import work?"}).json()
    return client, f"/api/projects/{project['id']}/sources"


BIB = "@article{a, title={First}, author={Lee, Ann}, year={2020}, doi={10.1234/a}}\n@article{b, year={2001}}"


def test_import_creates_unverified_sources_and_reports_the_entry_it_could_not_use():
    client, base = _project()
    result = client.post(f"{base}/import", json={"format": "bibtex", "text": BIB}).json()
    assert result["created"] == 1 and result["skipped"] == ["b: no title"]
    source = client.get(base).json()[0]
    assert source["title"] == "First" and source["metadata_verified"] is False


def test_importing_the_same_doi_twice_does_not_duplicate_it():
    client, base = _project()
    client.post(f"{base}/import", json={"format": "bibtex", "text": BIB})
    again = client.post(f"{base}/import", json={"format": "bibtex", "text": BIB}).json()
    assert again["created"] == 0 and any("DOI already" in s for s in again["skipped"])
    assert len(client.get(base).json()) == 1


def test_export_contains_only_verified_sources_and_counts_the_rest():
    client, base = _project()
    client.post(f"{base}/import", json={"format": "ris", "text": "TY  - JOUR\nTI  - Unchecked\nER  - \n"})
    verified = client.post(base, json={"title": "Checked paper"}).json()
    client.post(f"{base}/{verified['id']}/verify")
    for fmt, marker in (("bibtex", "Checked paper"), ("ris", "TI  - Checked paper")):
        response = client.get(f"{base}/export", params={"format": fmt})
        assert marker in response.text and "Unchecked" not in response.text
        assert response.headers["X-Unverified-Skipped"] == "1"


def test_import_and_export_are_audited():
    client, base = _project()
    client.post(f"{base}/import", json={"format": "bibtex", "text": BIB})
    client.get(f"{base}/export")
    actions = [e["action"] for e in client.get(base.replace("/sources", "/audit")).json()]
    assert "sources.imported" in actions and "sources.exported" in actions
