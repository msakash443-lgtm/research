import pytest

from app.genre_templates import GenreError, check_keywords, check_sections, outline


def test_every_genre_has_an_outline_that_starts_with_an_introduction():
    for genre in ("imrad", "conceptual", "review", "thesis_chapter"):
        sections = outline(genre)
        assert sections[0] == "Introduction"
        assert len(sections) == len(set(sections))


def test_imrad_outline_is_introduction_methods_results_discussion():
    assert outline("imrad") == ["Introduction", "Methods", "Results", "Discussion"]


def test_unknown_genre_is_refused():
    with pytest.raises(GenreError):
        outline("blog_post")


def test_a_draft_with_every_section_in_order_has_no_problems():
    problems = check_sections("imrad", ["Introduction", "Methods", "Results", "Discussion"])
    assert problems == {"missing": [], "out_of_order": False, "unexpected": []}


def test_a_missing_section_is_listed():
    problems = check_sections("imrad", ["Introduction", "Results", "Discussion"])
    assert problems["missing"] == ["Methods"]


def test_sections_out_of_order_are_flagged_without_inventing_a_fix():
    problems = check_sections("imrad", ["Introduction", "Results", "Methods", "Discussion"])
    assert problems["out_of_order"] is True
    assert problems["missing"] == []


def test_an_extra_section_is_reported_as_unexpected_not_refused():
    problems = check_sections("imrad", ["Introduction", "Methods", "Results", "Discussion", "Limitations"])
    assert problems["unexpected"] == ["Limitations"]


def test_keywords_must_be_non_blank_unique_and_within_the_limit():
    assert check_keywords(["fatigue", "Fatigue ", "sleep"], max_count=5) == ["duplicate keyword: 'Fatigue'"]
    assert check_keywords(["fatigue", "  "], max_count=5) == ["keyword 2 is blank"]
    assert check_keywords(["a", "b", "c"], max_count=2) == ["3 keywords; the limit is 2"]
    assert check_keywords(["fatigue", "sleep"], max_count=5) == []
