import pytest
from pydantic import ValidationError

from app.config import Settings

# Review finding (2), M5.6: the example value copied from .env.example was accepted at startup, so pseudonyms
# would be keyed with a public string. A placeholder or an empty key must stop the server from starting.


@pytest.mark.parametrize("bad", ["replace-with-a-long-random-secret", "replace-with-anything", "", "   "])
def test_placeholder_or_empty_pseudonym_key_refuses_startup(bad):
    with pytest.raises(ValidationError, match="PSEUDONYM_KEY"):
        Settings(_env_file=None, pseudonym_key=bad)



@pytest.mark.parametrize("short", ["x", "abcdefghijklmno"])
def test_a_pseudonym_key_shorter_than_16_characters_refuses_startup(short):
    # X.93: a short key lets a guessed key reverse pseudonyms of common names, so the floor matches ARC_RETRIEVAL_TOKEN.
    with pytest.raises(ValidationError, match="PSEUDONYM_KEY"):
        Settings(_env_file=None, pseudonym_key=short)


def test_a_pseudonym_key_of_exactly_16_characters_starts():
    assert Settings(_env_file=None, pseudonym_key="abcdefghijklmnop").pseudonym_key is not None

def test_a_real_pseudonym_key_starts_and_stays_secret():
    settings = Settings(_env_file=None, pseudonym_key="k8Qv2x0mZ-owner-chosen-key-not-a-placeholder")
    assert settings.pseudonym_key.get_secret_value().startswith("k8Qv2x0m")
    assert "k8Qv2x0m" not in repr(settings.pseudonym_key)


def test_unset_pseudonym_key_still_starts_the_server():
    # Unset is allowed: the route answers 503 instead (datasets.py), so the rest of the app keeps running.
    assert Settings(_env_file=None).pseudonym_key is None
