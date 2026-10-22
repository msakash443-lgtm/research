import pytest

from app.submissions import integration_enabled, transition


def test_submission_lifecycle_requires_explicit_valid_transitions():
    assert transition("draft", "submitted") == "submitted"
    assert transition("under_review", "accepted") == "accepted"
    with pytest.raises(ValueError):
        transition("draft", "accepted")


def test_external_integrations_are_off_by_default():
    assert integration_enabled({}, "orcid") is False
    assert integration_enabled({"zenodo_enabled": True}, "zenodo") is True
