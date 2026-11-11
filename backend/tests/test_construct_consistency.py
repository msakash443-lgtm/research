import pytest

from app.construct_consistency import find_spelling_variants


def test_hyphenated_and_spaced_spellings_of_one_construct_are_flagged_with_their_sections():
    variants = find_spelling_variants(
        {
            "introduction": "We study self-efficacy in adults.",
            "results": "Self efficacy rose after training.",
        }
    )

    assert variants == [
        {
            "construct": "self efficacy",
            "spellings": {
                "self efficacy": ["results"],
                "self-efficacy": ["introduction"],
            },
        }
    ]


@pytest.mark.parametrize(
    "sections",
    [
        pytest.param(
            {"a": "self-efficacy here.", "b": "self-efficacy there."},
            id="the-same-spelling-in-every-section",
        ),
        pytest.param(
            {"a": "Self-efficacy was high.", "b": "self-efficacy was low."},
            id="a-capital-at-the-start-of-a-sentence",
        ),
        pytest.param(
            {"a": "We measured self. Efficacy later.", "b": "self efficacy"},
            id="a-sentence-break-between-the-two-words",
        ),
    ],
)
def test_sections_that_only_look_like_a_variant_are_not_flagged(sections):
    assert find_spelling_variants(sections) == []


def test_a_three_word_construct_with_a_hyphen_variant_is_flagged():
    variants = find_spelling_variants(
        {"a": "well-being in adults matters.", "b": "well being in adults matters."}
    )

    assert "well being in adults" in [variant["construct"] for variant in variants]


def test_no_sections_give_no_variants():
    assert find_spelling_variants({}) == []
