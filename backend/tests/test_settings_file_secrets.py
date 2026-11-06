import pytest
from pydantic import ValidationError

from app.config import Settings, _file_secret_fields

SECRET = "file-secret-value-that-is-long-enough-1234"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("SESSION_SECRET", "SESSION_SECRET_FILE", "DATABASE_URL", "DATABASE_URL_FILE", "LLM_API_KEY_FILE", "LLM_MODEL_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_secret_is_read_from_file_and_newline_stripped(monkeypatch, tmp_path):
    path = tmp_path / "session_secret"
    path.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("SESSION_SECRET_FILE", str(path))
    assert Settings(_env_file=None).session_secret.get_secret_value() == SECRET


def test_database_url_can_come_from_file(monkeypatch, tmp_path):
    path = tmp_path / "db"
    path.write_text("postgresql+psycopg://u:p@db:5432/app?sslmode=require", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL_FILE", str(path))
    assert Settings(_env_file=None).database_url.endswith("/app?sslmode=require")


def test_missing_file_fails_without_leaking_anything(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_SECRET_FILE", str(tmp_path / "absent"))
    with pytest.raises(ValidationError, match="SESSION_SECRET_FILE points at a file that cannot be read"):
        Settings(_env_file=None)


def test_setting_both_is_refused_and_value_not_quoted(monkeypatch, tmp_path):
    path = tmp_path / "s"
    path.write_text(SECRET, encoding="utf-8")
    monkeypatch.setenv("SESSION_SECRET_FILE", str(path))
    monkeypatch.setenv("SESSION_SECRET", "direct-value-should-not-be-echoed-xxxx")
    with pytest.raises(ValidationError, match="not both") as error:
        Settings(_env_file=None)
    assert SECRET not in str(error.value)
    assert "direct-value-should-not-be-echoed" not in str(error.value)


def test_non_secret_fields_do_not_honour_file_variables(monkeypatch, tmp_path):
    path = tmp_path / "model"
    path.write_text("some-model", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL_FILE", str(path))
    assert Settings(_env_file=None).llm_model != "some-model"
    assert "llm_model" not in _file_secret_fields(Settings)
    assert {"database_url", "session_secret", "llm_api_key", "google_client_secret"} <= set(_file_secret_fields(Settings))
