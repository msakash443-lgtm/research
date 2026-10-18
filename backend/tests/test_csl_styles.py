"""Pure tests for the CSL-style citation/reference formatting registry (M6.5)."""

from app import csl_styles as cs
from app.manuscript_export import CitedSource

TWO_AUTHORS = CitedSource("1", "Wages after remote work", ("Smith, Jane", "Lee, Ann"), 2021, "Labour Journal", "10.1/abc", True)
NO_YEAR = CitedSource("2", "Untitled study", ("Jane Smith",), None, None, None, True)
NO_AUTHORS = CitedSource("3", "Anonymous report", (), 2020, None, None, True)


def test_unset_or_unknown_style_id_falls_back_to_default():
    assert cs.style_for(None).id == cs.DEFAULT_STYLE
    assert cs.style_for("harvard-nonexistent").id == cs.DEFAULT_STYLE


def test_default_style_matches_the_pre_m6_5_plain_author_year_format():
    style = cs.style_for(None)
    assert style.label(TWO_AUTHORS, 1) == "(Smith 2021)"
    assert style.reference(TWO_AUTHORS, 1) == "Smith, Jane; Lee, Ann (2021). Wages after remote work. Labour Journal. https://doi.org/10.1/abc"
    assert not style.numbered


def test_apa_style_adds_a_comma_before_the_year_and_an_ampersand_in_the_reference():
    style = cs.style_for("apa")
    assert style.label(TWO_AUTHORS, 1) == "(Smith, 2021)"
    assert style.reference(TWO_AUTHORS, 1) == "Smith, Jane, & Lee, Ann (2021). Wages after remote work. Labour Journal. https://doi.org/10.1/abc"
    assert style.label(NO_YEAR, 1) == "(Smith, n.d.)"


def test_chicago_author_date_has_no_comma_and_a_quoted_title():
    style = cs.style_for("chicago-author-date")
    assert style.label(TWO_AUTHORS, 1) == "(Smith 2021)"
    assert style.reference(TWO_AUTHORS, 1) == 'Smith, Jane; Lee, Ann. 2021. "Wages after remote work." Labour Journal. https://doi.org/10.1/abc'


def test_mla_in_text_marker_is_author_only_no_page_data_stored():
    for style_id in ("modern-language-association", "mla"):
        style = cs.style_for(style_id)
        assert style.label(TWO_AUTHORS, 1) == "(Smith)"
        assert style.reference(TWO_AUTHORS, 1).startswith('Smith, Jane; Lee, Ann. "Wages after remote work."')


def test_vancouver_and_ieee_are_numbered_with_a_bracketed_index():
    for style_id in ("vancouver", "ieee"):
        style = cs.style_for(style_id)
        assert style.numbered
        assert style.label(TWO_AUTHORS, 1) == "[1]"
        assert style.label(TWO_AUTHORS, 7) == "[7]"
        assert style.reference(TWO_AUTHORS, 3).startswith("3. Smith, Jane, Lee, Ann. Wages after remote work.")


def test_missing_authors_fall_back_to_anonymous_or_the_title():
    assert cs.style_for("apa").label(NO_AUTHORS, 1) == "(Anonymous report, 2020)"
    assert cs.style_for("apa").reference(NO_AUTHORS, 1).startswith("Anonymous (2020).")


def test_known_styles_lists_every_registered_id_sorted():
    assert cs.known_styles() == sorted(cs.known_styles())
    assert {"default", "apa", "chicago-author-date", "modern-language-association", "mla", "vancouver", "ieee"} <= set(cs.known_styles())
