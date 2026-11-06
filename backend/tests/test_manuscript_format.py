import pytest

from app.genre_templates import GenreError
from app.manuscript_format import check_figure_specs, check_word_limits, missing_sections, word_count


def test_word_count_splits_on_whitespace():
    assert word_count("  Sleep  and   fatigue\nin nurses ") == 5
    assert word_count("") == 0


def test_sections_within_their_limit_have_no_problems():
    problems = check_word_limits({"Abstract": "one two three"}, {"Abstract": 3})
    assert problems == []


def test_a_section_over_its_limit_is_listed_with_both_counts():
    problems = check_word_limits({"Abstract": "one two three four"}, {"Abstract": 3})
    assert problems == [{"section": "Abstract", "words": 4, "limit": 3}]


def test_the_total_limit_is_checked_across_all_sections():
    draft = {"Introduction": "a b c", "Discussion": "d e"}
    problems = check_word_limits(draft, {"total": 4})
    assert problems == [{"section": "total", "words": 5, "limit": 4}]


def test_a_limit_for_a_section_that_is_not_in_the_draft_is_refused():
    with pytest.raises(ValueError):
        check_word_limits({"Abstract": "one"}, {"Abstrct": 10})


def test_a_limit_must_be_a_positive_whole_number():
    with pytest.raises(ValueError):
        check_word_limits({"Abstract": "one"}, {"Abstract": 0})


def test_an_outline_section_the_draft_has_no_heading_for_is_reported_as_missing():
    assert missing_sections("imrad", ["Introduction", "Methods", "Results"]) == ["Discussion"]


def test_a_draft_with_every_outline_section_has_nothing_missing():
    assert missing_sections("review", ["Introduction", "Method", "Findings", "Discussion"]) == []


def test_an_unknown_genre_is_refused_rather_than_passed():
    with pytest.raises(GenreError):
        missing_sections("poster", ["Introduction"])


def test_a_figure_wider_than_the_guideline_is_flagged_on_width():
    figures = [{"name": "Figure 1", "width_mm": 180, "dpi": 300}]
    assert check_figure_specs(figures, {"max_width_mm": 170}) == [
        {"figure": "Figure 1", "measure": "width_mm", "value": 180, "limit": 170}
    ]


def test_a_figure_below_the_minimum_resolution_is_flagged_on_dpi():
    figures = [{"name": "Figure 2", "width_mm": 80, "dpi": 150}]
    assert check_figure_specs(figures, {"min_dpi": 300}) == [
        {"figure": "Figure 2", "measure": "dpi", "value": 150, "limit": 300}
    ]


def test_a_figure_within_both_limits_is_not_flagged():
    figures = [{"name": "Figure 3", "width_mm": 170, "dpi": 300}]
    assert check_figure_specs(figures, {"max_width_mm": 170, "min_dpi": 300}) == []


def test_a_limit_the_author_leaves_out_is_not_checked():
    figures = [{"name": "Figure 4", "width_mm": 999, "dpi": 1}]
    assert check_figure_specs(figures, {}) == []


def test_an_unknown_figure_limit_key_is_refused():
    with pytest.raises(ValueError):
        check_figure_specs([], {"max_height_mm": 200})


@pytest.mark.parametrize("limit", [0, -5, True, "300"])
def test_a_figure_limit_that_is_not_a_positive_number_is_refused(limit):
    with pytest.raises(ValueError):
        check_figure_specs([], {"min_dpi": limit})


def test_a_figure_without_a_positive_resolution_is_refused_rather_than_passed():
    with pytest.raises(ValueError):
        check_figure_specs([{"name": "Figure 5", "width_mm": 80, "dpi": 0}], {"min_dpi": 300})
