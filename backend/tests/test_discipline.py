"""Plan M0.7: discipline profiles are validated configuration (spec principle 6), not code."""

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app import discipline as d
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ProjectMember, ProjectRole


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    """Point the loader at an empty directory so a test can write its own profile files."""
    monkeypatch.setattr(d, "PROFILE_DIR", tmp_path)
    d._load.cache_clear()
    yield tmp_path
    d._load.cache_clear()


GOOD = '''name = "demo"
version = 1
label = "Demo"
description = "A demo profile."

[settings]
citation_style = "apa"
databases = ["openalex", "pubmed"]
methods = ["cohort_study"]
reporting_guideline = "STROBE"
'''


# ---- the shipped profiles ----------------------------------------------------------------------

def test_the_shipped_profiles_are_the_disciplines_the_product_owner_chose_and_are_valid():
    assert d.available_profiles() == [
        "commerce", "economics", "english_literature", "management", "psychology", "social_sciences",
    ]
    for name in d.available_profiles():
        profile = d.load_profile(name)
        assert profile.name == name and profile.version >= 1 and profile.label and profile.description
        s = profile.settings
        assert s.citation_style and s.databases and s.methods and s.reporting_guideline
        assert set(s.databases) <= d.KNOWN_DATABASES and s.reporting_guideline in d.KNOWN_REPORTING_GUIDELINES


def test_the_profiles_differ_in_the_ways_the_spec_says_fields_differ():
    economics = d.load_profile("economics").settings
    psychology = d.load_profile("psychology").settings
    english = d.load_profile("english_literature").settings

    for name in ("commerce", "economics", "management", "psychology", "social_sciences"):
        assert d.load_profile(name).settings.citation_style == "apa"
    assert english.citation_style == "modern-language-association"

    assert "psycinfo" in psychology.databases
    for name in ("commerce", "economics", "english_literature", "management", "social_sciences"):
        assert "psycinfo" not in d.load_profile(name).settings.databases
    assert "econlit" in economics.databases
    for name in ("commerce", "english_literature", "management", "psychology", "social_sciences"):
        assert "econlit" not in d.load_profile(name).settings.databases
    assert "mla_international_bibliography" in english.databases
    for name in ("commerce", "economics", "management", "psychology", "social_sciences"):
        assert "mla_international_bibliography" not in d.load_profile(name).settings.databases

    assert "difference_in_differences" in economics.methods
    assert "close_reading" in english.methods


# ---- loader strictness ---------------------------------------------------------------------------

def test_a_valid_profile_file_loads(profile_dir):
    (profile_dir / "demo.toml").write_text(GOOD, encoding="utf-8")

    profile = d.load_profile("demo")

    assert (profile.name, profile.version, profile.settings.databases) == ("demo", 1, ["openalex", "pubmed"])
    assert d.available_profiles() == ["demo"]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda s: s.replace('name = "demo"', 'name = "other"'), "declares name 'other'"),
        (lambda s: s.replace("version = 1", "version = 0"), "version of 1 or more"),
        (lambda s: s.replace('version = 1\n', ""), "invalid"),
        (lambda s: s.replace('label = "Demo"\n', ""), "invalid"),
        (lambda s: s.replace('"pubmed"', '"pubmd"'), "unknown database"),
        (lambda s: s.replace('"STROBE"', '"PRISMA2020"'), "unknown reporting guideline"),
        (lambda s: s.replace('citation_style = "apa"', 'citation_style = "APA Style"'), "CSL style id"),
        (lambda s: s + 'surprise = "x"\n', "invalid"),
        (lambda s: "this is = = not toml", "invalid"),
    ],
)
def test_a_malformed_profile_is_rejected_when_loaded(profile_dir, mutate, message):
    (profile_dir / "demo.toml").write_text(mutate(GOOD), encoding="utf-8")

    with pytest.raises(d.ProfileError, match=message):
        d.load_profile("demo")


@pytest.mark.parametrize("name", ["missing", "../etc/passwd", "Bad Name", ""])
def test_unknown_or_unsafe_profile_names_are_refused(name):
    with pytest.raises(d.ProfileError, match="does not exist"):
        d.load_profile(name)


# ---- settings validation ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        {"databases": ["openalex", "nonsense_db"]},
        {"databases": ["openalex", "openalex"]},
        {"databases": [f"openalex"] * 21},
        {"methods": ["Randomized Trial"]},
        {"methods": ["x"]},
        {"methods": ["cohort", "cohort"]},
        {"reporting_guideline": "prisma"},
        {"citation_style": "Chicago"},
        {"citation_style": "chicago author date"},
        {"citation_style": "x" * 81},
        {"unknown_setting": 1},
    ],
)
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(ValidationError):
        d.ProfileSettings(**bad)


def test_valid_settings_are_accepted_and_unset_fields_stay_none():
    s = d.ProfileSettings(databases=["scopus", "econlit"], citation_style="harvard-cite-them-right")

    assert s.methods is None and s.reporting_guideline is None
    assert s.model_dump(exclude_none=True) == {"citation_style": "harvard-cite-them-right", "databases": ["scopus", "econlit"]}


# ---- effective settings ----------------------------------------------------------------------------

def test_the_profile_alone_gives_its_settings_and_reports_which_profile_and_version():
    e = d.effective_settings("economics", None)

    assert (e.profile, e.profile_version) == ("economics", 2)
    assert e.databases == d.load_profile("economics").settings.databases and e.citation_style == "apa"


def test_a_project_override_replaces_a_setting_and_lists_are_replaced_not_merged():
    e = d.effective_settings("economics", {"databases": ["openalex"], "reporting_guideline": "MOOSE"})

    assert e.databases == ["openalex"]  # not merged with the profile's five
    assert e.reporting_guideline == "MOOSE"
    assert e.citation_style == "apa" and "instrumental_variables" in e.methods  # the rest inherited


def test_without_a_profile_only_the_overrides_apply_and_lists_default_to_empty():
    assert d.effective_settings(None, None).model_dump() == {
        "profile": None, "profile_version": None, "citation_style": None, "databases": [], "methods": [], "reporting_guideline": None,
    }
    assert d.effective_settings(None, {"citation_style": "apa"}).citation_style == "apa"


def test_an_unknown_profile_or_corrupt_stored_overrides_raise_instead_of_guessing():
    with pytest.raises(d.ProfileError):
        d.effective_settings("deleted_profile", None)
    with pytest.raises(ValidationError):
        d.effective_settings("economics", {"databases": ["nonsense_db"]})


# ---- the API ---------------------------------------------------------------------------------------

def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "D"}).json()["id"]
    return client, user_id


def _project(email, **extra):
    client, user_id = _login(email)
    response = client.post("/api/projects", json={"title": "Profiled", **extra})
    return client, user_id, response


def test_the_catalogue_lists_the_profiles_but_only_to_a_signed_in_user():
    assert TestClient(app).get("/api/discipline-profiles").status_code == 401
    client, _ = _login("disc-catalogue@example.com")

    body = client.get("/api/discipline-profiles").json()

    by_name = {p["name"]: p for p in body}
    assert list(by_name) == ["commerce", "economics", "english_literature", "management", "psychology", "social_sciences"]
    assert by_name["economics"]["settings"]["databases"] and by_name["economics"]["version"] == 2 and by_name["economics"]["label"] == "Economics"


def test_a_project_can_start_with_a_discipline_and_an_unknown_one_is_refused():
    _c, _u, ok = _project("disc-create-ok@example.com", discipline="psychology")
    _c, _u, bad = _project("disc-create-bad@example.com", discipline="astrology")
    _c, _u, none = _project("disc-create-none@example.com")

    assert ok.status_code == 201 and ok.json()["discipline"] == "psychology"
    assert bad.status_code == 422 and "astrology" in bad.text and "economics" in bad.text  # the message lists the options
    assert none.status_code == 201 and none.json()["discipline"] is None


def test_a_project_without_a_profile_has_empty_effective_settings():
    client, _u, created = _project("disc-none@example.com")

    body = client.get(f"/api/projects/{created.json()['id']}/profile").json()

    assert body["discipline"] is None and body["label"] is None and body["overrides"] == {}
    assert body["effective"]["databases"] == [] and body["effective"]["profile"] is None


def test_the_owner_sets_a_profile_with_overrides_and_the_effective_settings_combine_them():
    client, owner_id, created = _project("disc-set@example.com")
    pid = created.json()["id"]

    response = client.put(
        f"/api/projects/{pid}/profile",
        json={"discipline": "economics", "overrides": {"databases": ["openalex", "repec"], "citation_style": "chicago-author-date"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["discipline"] == "economics" and body["label"] == "Economics"
    assert body["overrides"] == {"citation_style": "chicago-author-date", "databases": ["openalex", "repec"]}  # only what was set
    assert body["effective"]["databases"] == ["openalex", "repec"] and body["effective"]["citation_style"] == "chicago-author-date"
    assert body["effective"]["reporting_guideline"] == "PRISMA-2020" and body["effective"]["profile_version"] == 2
    assert client.get(f"/api/projects/{pid}").json()["discipline"] == "economics"
    assert client.get(f"/api/projects/{pid}/profile").json() == body


def test_putting_a_profile_replaces_the_previous_choice_and_overrides_and_null_clears_it():
    client, _u, created = _project("disc-replace@example.com", discipline="economics")
    pid = created.json()["id"]
    client.put(f"/api/projects/{pid}/profile", json={"discipline": "economics", "overrides": {"methods": ["panel_fixed_effects"]}})

    switched = client.put(f"/api/projects/{pid}/profile", json={"discipline": "psychology"}).json()

    assert switched["discipline"] == "psychology" and switched["overrides"] == {}  # old overrides gone
    assert "pubmed" in switched["effective"]["databases"]
    cleared = client.put(f"/api/projects/{pid}/profile", json={"discipline": None}).json()
    assert cleared["discipline"] is None and cleared["effective"]["databases"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"discipline": "astrology"},
        {"discipline": "economics", "overrides": {"databases": ["nonsense_db"]}},
        {"discipline": "economics", "overrides": {"surprise": 1}},
        {"discipline": "economics", "overrides": {"reporting_guideline": "prisma"}},
        {"discipline": "economics", "unexpected": True},
    ],
)
def test_an_invalid_profile_is_rejected_and_the_project_is_unchanged(bad):
    client, _u, created = _project("disc-invalid@example.com", discipline="psychology")
    pid = created.json()["id"]
    before = client.get(f"/api/projects/{pid}/profile").json()

    assert client.put(f"/api/projects/{pid}/profile", json=bad).status_code == 422

    assert client.get(f"/api/projects/{pid}/profile").json() == before


def test_only_the_owner_may_change_the_profile_and_everyone_on_the_project_can_read_it():
    owner, _o, created = _project("disc-roles-owner@example.com", discipline="economics")
    pid = created.json()["id"]
    clients = {}
    for role in ("supervisor", "co_author", "reviewer"):
        client, uid = _login(f"disc-roles-{role}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), role=ProjectRole(role)))
            db.commit()
        clients[role] = client

    for role, client in clients.items():
        assert client.put(f"/api/projects/{pid}/profile", json={"discipline": "psychology"}).status_code == 403
        assert client.get(f"/api/projects/{pid}/profile").json()["discipline"] == "economics"


def test_a_profile_change_is_audited_with_the_before_and_after():
    client, owner_id, created = _project("disc-audit@example.com", discipline="economics")
    pid = created.json()["id"]

    client.put(f"/api/projects/{pid}/profile", json={"discipline": "psychology", "overrides": {"methods": ["meta_analysis"]}})

    with SessionLocal() as db:
        (event,) = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "project.profile_changed")).all()
    assert event.actor == owner_id
    assert event.payload_json == {
        "from": {"discipline": "economics", "overrides": {}},
        "to": {"discipline": "psychology", "overrides": {"methods": ["meta_analysis"]}},
    }


def test_a_stored_profile_that_no_longer_exists_does_not_make_the_project_unreadable(tmp_path, monkeypatch):
    client, _u, created = _project("disc-removed@example.com", discipline="economics")  # created while it exists...
    pid = created.json()["id"]
    monkeypatch.setattr(d, "PROFILE_DIR", tmp_path)  # ...then the shipped set changes underneath it
    d._load.cache_clear()
    (tmp_path / "demo.toml").write_text(GOOD, encoding="utf-8")
    try:
        assert client.get(f"/api/projects/{pid}").status_code == 200  # stored data is always readable
        assert client.get("/api/projects").status_code == 200

        response = client.get(f"/api/projects/{pid}/profile")

        assert response.status_code == 409 and "does not exist" in response.json()["detail"]
        fixed = client.put(f"/api/projects/{pid}/profile", json={"discipline": "demo"})
        assert fixed.status_code == 200 and fixed.json()["effective"]["profile"] == "demo"
    finally:
        d._load.cache_clear()


def test_existing_projects_with_no_profile_are_unaffected():
    with SessionLocal() as db:
        from app.models import User

        user = User(email=f"disc-legacy-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Legacy")
        db.add(project)
        db.commit()
        assert (project.discipline, project.config_json) == (None, None)
