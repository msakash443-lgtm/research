from fastapi.testclient import TestClient

from app.main import app


def test_local_password_registration_and_login():
    client = TestClient(app)
    registered = client.post(
        "/api/auth/register",
        json={"email": "secure@example.com", "password": "StrongPass123!", "display_name": "Secure User"},
    )
    assert registered.status_code == 200
    assert registered.json()["email"] == "secure@example.com"

    client.post("/api/auth/logout")
    logged_in = client.post(
        "/api/auth/login",
        json={"email": "secure@example.com", "password": "StrongPass123!"},
    )
    assert logged_in.status_code == 200

    invalid = client.post(
        "/api/auth/login",
        json={"email": "secure@example.com", "password": "wrong-password"},
    )
    assert invalid.status_code == 401
