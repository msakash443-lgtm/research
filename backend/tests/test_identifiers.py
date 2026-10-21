import pytest

from app.identifiers import normalize_orcid


@pytest.mark.parametrize(
    "raw",
    [
        "0000-0002-1825-0097",
        "https://orcid.org/0000-0002-1825-0097",
        "http://orcid.org/0000-0002-1825-0097",
        "orcid.org/0000-0002-1825-0097",
        "  0000-0002-1825-0097  ",
    ],
)
def test_normalize_orcid_accepts_bare_and_link_forms(raw):
    assert normalize_orcid(raw) == "0000-0002-1825-0097"


def test_normalize_orcid_upper_cases_a_check_digit_x():
    assert normalize_orcid("0000-0002-1694-233x") == "0000-0002-1694-233X"


def test_normalize_orcid_blank_is_none():
    assert normalize_orcid(None) is None
    assert normalize_orcid("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        "0000-0002-1825-0098",  # wrong check digit
        "0000-0002-1825-009",  # too short
        "0000-0002-1825-00977",  # too long
        "0000 0002 1825 0097",  # wrong separators
        "0000-0002-1825-009X",  # X where a digit is required by the checksum
        "not-an-orcid",
    ],
)
def test_normalize_orcid_refuses_malformed_or_bad_checksum(raw):
    with pytest.raises(ValueError):
        normalize_orcid(raw)
