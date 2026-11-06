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


def test_the_same_spelling_in_every_section_is_not_flagged():
    assert find_spelling_variants({"a": "self-efficacy here.", "b": "self-efficacy there."}) == []


def test_a_capital_at_the_start_of_a_sentence_is_not_a_variant():
    assert find_spelling_variants({"a": "Self-efficacy was high.", "b": "self-efficacy was low."}) == []


def test_a_sentence_break_between_two_words_does_not_make_a_construct():
    assert find_spelling_variants({"a": "We measured self. Efficacy later.", "b": "self efficacy"}) == []


def test_a_three_word_construct_with_a_hyphen_variant_is_flagged():
    variants = find_spelling_variants(
        {"a": "well-being in adults matters.", "b": "well being in adults matters."}
    )

    assert "well being in adults" in [variant["construct"] for variant in variants]


def test_no_sections_give_no_variants():
    assert find_spelling_variants({}) == []
