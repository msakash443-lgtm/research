"""Obsidian vault export (plan X.34.1): verified only, audited, and inert in Obsidian.

Runs go through the real pipeline (fake LLM endpoint); the export is read back from the zip.
"""

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from adversarial_fixtures import FIXTURES
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Source
from app.obsidian_export import fenced_text, safe_markdown, safe_name
from tests.llm_replies import abstention, chat_reply


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "obsidian_export_enabled", True)


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "O"})
    return client


@pytest.fixture
def world():
    c = _login(f"obs-{uuid.uuid4().hex[:8]}@example.com")
    pid = c.post("/api/projects", json={"title": "Remote work: a review"}).json()["id"]
    return c, pid


def add_source(c, pid, title, verify=True, **over):
    body = {"title": title, "authors": ["Ada Lovelace"], "year": 2020, "evidence_excerpt": "Productivity rose 13%.", **over}
    response = c.post(f"/api/projects/{pid}/sources", json=body)
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    if verify:
        assert c.post(f"/api/projects/{pid}/sources/{sid}/verify").status_code == 200
    return sid


def research(c, pid, fake_llm, answer, question="Does remote work raise productivity?"):
    fake_llm.handler = lambda request: chat_reply(answer)
    response = c.post(f"/api/projects/{pid}/research-runs", json={"question": question})
    assert response.status_code == 202, response.text
    run = c.get(f"/api/projects/{pid}/research-runs").json()
    return next(r for r in run if r["id"] == response.json()["id"])


def export(c, pid):
    response = c.get(f"/api/projects/{pid}/export/obsidian")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def frontmatter(note):
    block = note.split("---\n")[1]
    return {key: json.loads(value) for key, value in (line.split(": ", 1) for line in block.strip().splitlines())}


def notes_in(files, folder):
    return {path.split("/", 2)[2]: text for path, text in files.items() if path.split("/")[1] == folder}


def test_off_by_default(world):
    c, pid = world
    assert c.get(f"/api/projects/{pid}/export/obsidian").status_code == 404


def test_vault_has_index_context_and_only_verified_sources(world, enabled):
    c, pid = world
    c.post(f"/api/projects/{pid}/context", json={"kind": "question", "content": "Does remote work raise productivity?"})
    c.post(f"/api/projects/{pid}/context", json={"kind": "idea", "content": "Look at hybrid schedules"})
    add_source(c, pid, "Verified study", doi="10.1234/v")
    add_source(c, pid, "Unverified study", verify=False)

    files = export(c, pid)

    assert all(path.startswith("Remote work a review/") for path in files)
    sources = notes_in(files, "Sources")
    assert list(sources) == ["Lovelace 2020 - Verified study.md"]
    fm = frontmatter(sources["Lovelace 2020 - Verified study.md"])
    assert fm["verified"] is True and fm["doi"] == "10.1234/v" and fm["authors"] == ["Ada Lovelace"] and fm["tags"] == ["source"]
    assert len(notes_in(files, "Context")) == 2
    index = files["Remote work a review/_Index.md"]
    assert "[[Sources/Lovelace 2020 - Verified study]]" in index
    assert "Left out: 1 unverified or merged source(s)" in index


def test_merged_and_automatic_unverified_sources_are_left_out(world, enabled):
    c, pid = world
    keep = add_source(c, pid, "Kept", doi="10.1234/k")
    merged = add_source(c, pid, "Merged away", doi="10.1234/m")
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(merged)).merged_into = uuid.UUID(keep)
        db.commit()

    assert [name for name in notes_in(export(c, pid), "Sources")] == ["Lovelace 2020 - Kept.md"]


def test_completed_run_citing_verified_sources_links_them(world, enabled, fake_llm):
    c, pid = world
    add_source(c, pid, "First study", doi="10.1234/a")
    add_source(c, pid, "Second study", doi="10.1234/b", authors=["Grace Hopper"])
    run = research(c, pid, fake_llm, "Productivity rose [S1, S2] and stayed up [S2].")
    assert run["status"] == "completed"

    files = export(c, pid)
    (name, note), = notes_in(files, "Runs").items()
    assert "[[Sources/Lovelace 2020 - First study|S1]], [[Sources/Hopper 2020 - Second study|S2]]" in note
    assert "stayed up [[Sources/Hopper 2020 - Second study|S2]]" in note
    fm = frontmatter(note)
    assert fm["model"] == "fake-model" and fm["prompt_version"] and fm["tags"] == ["research-run"]


def test_runs_that_are_not_clean_are_left_out_and_counted(world, enabled, fake_llm):
    c, pid = world
    good = add_source(c, pid, "Good", doi="10.1234/g")
    research(c, pid, fake_llm, "Cites a verified source [S1].", question="Question one, long enough?")
    add_source(c, pid, "Unchecked", doi="10.1234/u", verify=False)
    blocked = research(c, pid, fake_llm, "Cites the unverified one [S2].", question="Question two, long enough?")
    assert blocked["status"] == "failed"  # M1.10.3 already blocks it at run time
    later = research(c, pid, fake_llm, "Cites the good one [S1].", question="Question three, long enough?")
    assert later["status"] == "completed"
    with SessionLocal() as db:  # the source behind every completed run stops being verified
        db.get(Source, uuid.UUID(good)).metadata_verified = False
        db.commit()

    files = export(c, pid)

    assert notes_in(files, "Runs") == {}
    event = c.get(f"/api/projects/{pid}/audit", params={"action": "project.exported"}).json()[0]
    assert event["payload_json"]["runs"] == 0
    assert event["payload_json"]["skipped_runs"] == 3
    assert event["payload_json"]["skipped_run_reasons"] == {"source_no_longer_verified": 2, "status_failed": 1}



def test_a_run_where_the_model_abstained_is_counted_as_such(world, enabled, fake_llm):
    c, pid = world
    add_source(c, pid, "Only study", doi="10.1234/o")
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(abstention())}}]}
    response = c.post(f"/api/projects/{pid}/research-runs", json={"question": "Does remote work raise productivity?"})
    assert response.json()["status"] == "completed" and response.json()["insufficient_evidence"] is True

    files = export(c, pid)

    assert notes_in(files, "Runs") == {}
    event = c.get(f"/api/projects/{pid}/audit", params={"action": "project.exported"}).json()[0]
    assert event["payload_json"]["skipped_run_reasons"] == {"insufficient_evidence": 1}
    index = next(text for path, text in files.items() if path.endswith("/_Index.md"))
    assert "found the evidence insufficient" in index

def test_export_is_audited_with_counts(world, enabled):
    c, pid = world
    add_source(c, pid, "One", doi="10.1234/1")
    export(c, pid)
    event = c.get(f"/api/projects/{pid}/audit", params={"action": "project.exported"}).json()
    assert len(event) == 1
    assert event[0]["payload_json"]["format"] == "obsidian" and event[0]["payload_json"]["sources"] == 1


def test_download_name_is_ascii(enabled):
    c = _login("obs-ascii@example.com")
    pid = c.post("/api/projects", json={"title": "Étude: «travail» à distance"}).json()["id"]
    response = c.get(f"/api/projects/{pid}/export/obsidian")
    assert response.status_code == 200
    assert re.fullmatch(r'attachment; filename="[A-Za-z0-9._-]+-obsidian\.zip"', response.headers["content-disposition"])


# --- inert in Obsidian -----------------------------------------------------------------------

HOSTILE_ANSWER = """Findings [S1].

```dataviewjs
dv.paragraph(app.vault.adapter.basePath)
```

<% tp.system.clipboard() %> and <script>alert(1)</script> and <img src=x onerror=alert(1)>
`$= app.vault.getFiles()` and `= this.file.name`
![[secret note]] ![tracker](https://evil.example/pixel.png)
~~~dataview
TABLE file.name
~~~
```js
unterminated"""


def _executable(note):
    """Anything Obsidian (core, Dataview, Templater) would run, embed or render as HTML."""
    found = []
    if re.search(r"^\s*(```|~~~)\s*(dataview|dataviewjs|js|javascript|tpl|templater)\b", note, re.M | re.I):
        found.append("code block")
    if "<%" in note:
        found.append("templater")
    if re.search(r"<\s*[a-z!/]", note, re.I):
        found.append("html")
    if re.search(r"(?<!\\)`\s*\$?=", note):
        found.append("inline query")
    if re.search(r"(?<!\\)!\[", note):
        found.append("embed")
    return found


def test_hostile_answer_is_inert(world, enabled, fake_llm):
    c, pid = world
    add_source(c, pid, "Study", doi="10.1234/s")
    assert research(c, pid, fake_llm, HOSTILE_ANSWER)["status"] == "completed"

    (note,) = notes_in(export(c, pid), "Runs").values()

    assert _executable(note) == []
    assert "dv.paragraph" in note  # kept as readable text, not run
    assert note.count("```") % 2 == 0  # the unterminated fence was closed


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_hostile_abstracts_and_titles_are_inert(world, enabled, fixture):
    c, pid = world
    sid = add_source(c, pid, "Plain title", doi="10.1234/h")
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(sid))
        source.abstract = fixture["text"] + "\n```dataviewjs\nalert(1)\n```\n<% tp.file %> ![[x]]"
        source.title = "Title <b>bold</b> ![[embed]] `= 1`"
        db.commit()

    (note,) = notes_in(export(c, pid), "Sources").values()

    # Frontmatter is data (Obsidian shows it as plain properties); the abstract sits in a plain text fence.
    body = note.split("---\n", 2)[2].split("## Abstract")[0]
    assert _executable(body) == []
    assert "<%" not in note
    fence = re.search(r"^(`{3,})text$", note, re.M).group(1)
    assert note.rstrip().endswith(fence) and note.count(fence + "\n") >= 1


def test_safe_markdown_and_fences_unit():
    assert _executable(safe_markdown(HOSTILE_ANSWER)) == []
    text = "a ```` b ``` c"
    assert fenced_text(text).startswith("`````text\n")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "etc passwd"),
        ('a:b*c?"d<e>f|g#h^i[j]k', "a b c d e f g h i j k"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("   ...   ", "Untitled"),
        ("x" * 300, "x" * 120),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_colliding_names_get_numbered(world, enabled):
    c, pid = world
    add_source(c, pid, "Same title", doi="10.1234/x")
    add_source(c, pid, "same TITLE", doi="10.1234/y")
    names = sorted(name.casefold() for name in notes_in(export(c, pid), "Sources"))
    assert names == ["lovelace 2020 - same title (2).md", "lovelace 2020 - same title.md"]  # whichever came first keeps the plain name


def test_zip_paths_stay_inside_the_vault(world, enabled):
    c, pid = world
    add_source(c, pid, "../../../escape", doi="10.1234/e")
    for path in export(c, pid):
        parts = path.split("/")
        assert ".." not in parts and not path.startswith("/") and "\\" not in path
