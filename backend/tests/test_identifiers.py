import pytest

from app.identifiers import normalize_orcid, normalize_ror


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


@pytest.mark.parametrize(
    "raw",
    [
        "٠٠٠٠-٠٠٠٢-١٦٩٤-٢٣٣X",  # Arabic-Indic digits
        "０００－０００２－１６９４－２３３Ｘ",  # full-width digits
        "0000-0002-1694-23\u0663X",  # one Arabic-Indic digit in place
    ],
)
def test_normalize_orcid_refuses_non_ascii_digits(raw):
    with pytest.raises(ValueError):
        normalize_orcid(raw)


def test_normalize_orcid_accepts_upper_case_link_prefix():
    assert normalize_orcid("HTTPS://ORCID.ORG/0000-0002-1825-0097") == "0000-0002-1825-0097"


def test_normalize_orcid_accepts_trailing_slash_on_link():
    assert normalize_orcid("https://orcid.org/0000-0002-1825-0097/") == "0000-0002-1825-0097"


@pytest.mark.parametrize(
    "raw",
    [
        "httpſ://orcid.org/0000-0002-1825-0097",  # long s case-folds to s under Unicode rules
        "ORCİD.org/0000-0002-1825-0097",  # dotted capital I case-folds to i
    ],
)
def test_normalize_orcid_refuses_non_ascii_link_prefix(raw):
    with pytest.raises(ValueError):
        normalize_orcid(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://ror.org/02mhbdp94",
        "ror.org/02mhbdp94",
        "02mhbdp94",
        "https://ror.org/02MHBDP94/",
        "  02mhbdp94  ",
    ],
)
def test_normalize_ror_accepts_full_url_short_and_bare_forms(raw):
    assert normalize_ror(raw) == "https://ror.org/02mhbdp94"


def test_normalize_ror_accepts_a_second_documented_id():
    assert normalize_ror("018mejw64") == "https://ror.org/018mejw64"


def test_normalize_ror_blank_is_none():
    assert normalize_ror(None) is None
    assert normalize_ror("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        "02mhbdp95",  # wrong check digits
        "12mhbdp94",  # no leading 0
        "02mhbdp9",  # too short
        "02mhbdp944",  # too long
        "02mhbdi94",  # I is excluded from Crockford base32
        "02mhbdl94",  # L is excluded from Crockford base32
        "https://ror.org/02mhbdp94x",  # letter where a check digit is required
        "not-a-ror",
    ],
)
def test_normalize_ror_refuses_malformed_or_bad_checksum(raw):
    with pytest.raises(ValueError):
        normalize_ror(raw)
