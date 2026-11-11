import pytest

from app.identifiers import normalize_orcid, normalize_ror


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param("0000-0002-1825-0097", "0000-0002-1825-0097", id="bare-form"),
        pytest.param(
            "https://orcid.org/0000-0002-1825-0097", "0000-0002-1825-0097", id="https-link-form"
        ),
        pytest.param(
            "http://orcid.org/0000-0002-1825-0097", "0000-0002-1825-0097", id="http-link-form"
        ),
        pytest.param(
            "orcid.org/0000-0002-1825-0097", "0000-0002-1825-0097", id="bare-link-form"
        ),
        pytest.param(
            "  0000-0002-1825-0097  ", "0000-0002-1825-0097", id="surrounding-whitespace"
        ),
        pytest.param(
            "0000-0002-1694-233x", "0000-0002-1694-233X", id="check-digit-x-is-upper-cased"
        ),
        pytest.param(
            "HTTPS://ORCID.ORG/0000-0002-1825-0097",
            "0000-0002-1825-0097",
            id="upper-case-link-prefix",
        ),
        pytest.param(
            "https://orcid.org/0000-0002-1825-0097/",
            "0000-0002-1825-0097",
            id="trailing-slash-on-link",
        ),
    ],
)
def test_normalize_orcid_accepts_valid_forms_and_normalizes_to_canonical(raw, expected):
    assert normalize_orcid(raw) == expected


def test_normalize_orcid_blank_is_none():
    assert normalize_orcid(None) is None
    assert normalize_orcid("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("0000-0002-1825-0098", id="wrong-check-digit"),
        pytest.param("0000-0002-1825-009", id="too-short"),
        pytest.param("0000-0002-1825-00977", id="too-long"),
        pytest.param("0000 0002 1825 0097", id="wrong-separators"),
        pytest.param("0000-0002-1825-009X", id="x-where-a-digit-is-required-by-the-checksum"),
        pytest.param("not-an-orcid", id="not-an-orcid"),
        pytest.param("٠٠٠٠-٠٠٠٢-١٦٩٤-٢٣٣X", id="arabic-indic-digits"),
        pytest.param("０００－０００２－１６９４－２３３Ｘ", id="full-width-digits"),
        pytest.param("0000-0002-1694-23\u0663X", id="one-arabic-indic-digit-in-place"),
        pytest.param(
            "httpſ://orcid.org/0000-0002-1825-0097",
            id="long-s-case-folds-to-s-under-unicode-rules",
        ),
        pytest.param(
            "ORCİD.org/0000-0002-1825-0097", id="dotted-capital-i-case-folds-to-i"
        ),
    ],
)
def test_normalize_orcid_refuses_malformed_or_non_ascii_input(raw):
    with pytest.raises(ValueError):
        normalize_orcid(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param(
            "https://ror.org/02mhbdp94", "https://ror.org/02mhbdp94", id="full-url-form"
        ),
        pytest.param("ror.org/02mhbdp94", "https://ror.org/02mhbdp94", id="bare-link-form"),
        pytest.param("02mhbdp94", "https://ror.org/02mhbdp94", id="bare-id-form"),
        pytest.param(
            "https://ror.org/02MHBDP94/", "https://ror.org/02mhbdp94", id="upper-case-with-trailing-slash"
        ),
        pytest.param(
            "  02mhbdp94  ", "https://ror.org/02mhbdp94", id="surrounding-whitespace"
        ),
        pytest.param(
            "018mejw64", "https://ror.org/018mejw64", id="a-second-documented-id"
        ),
    ],
)
def test_normalize_ror_accepts_valid_forms_and_normalizes_to_canonical(raw, expected):
    assert normalize_ror(raw) == expected


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
