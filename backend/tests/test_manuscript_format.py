import pytest

from app.manuscript_format import check_word_limits, word_count


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
