import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.ui_themes import registered_themes

# X.245: the default UI theme is a setting, validated against the theme files that exist.


def test_the_registered_themes_are_the_theme_files_without_the_template():
    assert registered_themes() == ("dense", "modern", "scholarly")


def test_the_default_theme_is_scholarly():
    assert Settings(_env_file=None).ui_theme == "scholarly"


def test_a_theme_without_a_file_refuses_to_start_and_names_the_choices():
    with pytest.raises(ValueError, match="UI_THEME must be one of: dense, modern, scholarly"):
        Settings(_env_file=None, ui_theme="neon")


def test_the_template_is_not_a_selectable_theme():
    with pytest.raises(ValueError, match="UI_THEME must be one of"):
        Settings(_env_file=None, ui_theme="_template")


@pytest.mark.real_db
def test_the_page_carries_the_configured_default_theme_and_the_registered_ones(monkeypatch):
    monkeypatch.setattr("app.main.settings.ui_theme", "dense")
    with TestClient(app) as client:
        page = client.get("/").text
    assert '<html lang="en" data-theme="dense" data-themes="dense modern scholarly"' in page
    assert "__UI_" not in page
    assert '<script src="/assets/js/theme-init.js"></script>' in page.split("</head>")[0]
