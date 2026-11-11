import pytest
from fastapi.testclient import TestClient

from app.main import app


def _login_project(email, title="People"):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "P"})
    pid = client.post("/api/projects", json={"title": title}).json()["id"]
    return client, pid


@pytest.mark.parametrize(
    "resource,email,field,value,expected",
    [
        pytest.param(
            "people",
            "people-orcid@example.com",
            "orcid",
            "https://orcid.org/0000-0002-1825-0097",
            "0000-0002-1825-0097",
            id="an-orcid-link-is-stored-as-the-bare-normalised-id",
        ),
        pytest.param(
            "institutions",
            "people-ror@example.com",
            "ror_id",
            "02mhbdp94",
            "https://ror.org/02mhbdp94",
            id="a-ror-id-is-stored-as-the-full-url",
        ),
    ],
)
def test_an_identifier_is_normalised_on_storage(resource, email, field, value, expected):
    client, pid = _login_project(email)
    response = client.post(f"/api/projects/{pid}/{resource}", json={"name": "Ada", field: value})
    assert response.status_code == 201
    assert response.json()[field] == expected


@pytest.mark.parametrize(
    "resource,email,field,value",
    [
        pytest.param(
            "people",
            "people-bad-orcid@example.com",
            "orcid",
            "0000-0002-1825-0098",
            id="a-malformed-orcid-is-refused-and-nothing-is-stored",
        ),
        pytest.param(
            "institutions",
            "people-bad-ror@example.com",
            "ror_id",
            "02mhbdp95",
            id="a-ror-id-with-a-wrong-check-digit-is-refused",
        ),
    ],
)
def test_a_malformed_identifier_is_refused_and_nothing_is_stored(resource, email, field, value):
    client, pid = _login_project(email)
    response = client.post(f"/api/projects/{pid}/{resource}", json={"name": "Ada", field: value})
    assert response.status_code == 422
    assert client.get(f"/api/projects/{pid}/{resource}").json() == []


def test_a_person_can_link_to_an_institution_of_the_same_project():
    client, pid = _login_project("people-link@example.com")
    institution = client.post(f"/api/projects/{pid}/institutions", json={"name": "Lab"}).json()
    response = client.post(
        f"/api/projects/{pid}/people", json={"name": "Ada", "institution_id": institution["id"]}
    )
    assert response.status_code == 201
    assert response.json()["institution_id"] == institution["id"]


def test_linking_to_an_institution_of_another_project_reads_as_not_found():
    owner, owner_pid = _login_project("people-owner@example.com", "Owner")
    institution = owner.post(f"/api/projects/{owner_pid}/institutions", json={"name": "Their lab"}).json()

    other, other_pid = _login_project("people-other@example.com", "Other")
    response = other.post(
        f"/api/projects/{other_pid}/people", json={"name": "Mallory", "institution_id": institution["id"]}
    )
    assert response.status_code == 404
    assert other.get(f"/api/projects/{other_pid}/people").json() == []


def test_people_and_institutions_are_listed_only_for_their_own_project():
    first, first_pid = _login_project("people-scope-a@example.com", "First")
    second, second_pid = _login_project("people-scope-b@example.com", "Second")
    first.post(f"/api/projects/{first_pid}/people", json={"name": "Only in first"})
    first.post(f"/api/projects/{first_pid}/institutions", json={"name": "Only in first"})

    assert [row["name"] for row in second.get(f"/api/projects/{second_pid}/people").json()] == []
    assert [row["name"] for row in second.get(f"/api/projects/{second_pid}/institutions").json()] == []
    assert [row["name"] for row in first.get(f"/api/projects/{first_pid}/people").json()] == ["Only in first"]
