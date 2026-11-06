from fastapi.testclient import TestClient

from app.main import app


def _login_project(email, title="People"):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "P"})
    pid = client.post("/api/projects", json={"title": title}).json()["id"]
    return client, pid


def test_an_orcid_link_is_stored_as_the_bare_normalised_id():
    client, pid = _login_project("people-orcid@example.com")
    response = client.post(
        f"/api/projects/{pid}/people",
        json={"name": "Ada", "orcid": "https://orcid.org/0000-0002-1825-0097"},
    )
    assert response.status_code == 201
    assert response.json()["orcid"] == "0000-0002-1825-0097"


def test_a_malformed_orcid_is_refused_and_nothing_is_stored():
    client, pid = _login_project("people-bad-orcid@example.com")
    response = client.post(f"/api/projects/{pid}/people", json={"name": "Ada", "orcid": "0000-0002-1825-0098"})
    assert response.status_code == 422
    assert client.get(f"/api/projects/{pid}/people").json() == []


def test_a_ror_id_is_stored_as_the_full_url():
    client, pid = _login_project("people-ror@example.com")
    response = client.post(f"/api/projects/{pid}/institutions", json={"name": "Lab", "ror_id": "02mhbdp94"})
    assert response.status_code == 201
    assert response.json()["ror_id"] == "https://ror.org/02mhbdp94"


def test_a_ror_id_with_a_wrong_check_digit_is_refused():
    client, pid = _login_project("people-bad-ror@example.com")
    response = client.post(f"/api/projects/{pid}/institutions", json={"name": "Lab", "ror_id": "02mhbdp95"})
    assert response.status_code == 422
    assert client.get(f"/api/projects/{pid}/institutions").json() == []


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
