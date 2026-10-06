import pytest
from pydantic import ValidationError

from app.search_query import (
    MAX_BLOCKS,
    MAX_QUERY_CHARS,
    MAX_TERM_CHARS,
    MAX_TERMS_PER_BLOCK,
    BooleanQuery,
    ConceptBlock,
    QueryError,
    normalise_term,
    render,
)


def block(*terms, label=None):
    return ConceptBlock(label=label, terms=terms)


def query(*blocks):
    return BooleanQuery(blocks=blocks)


def test_appendix_a_shape_or_within_and_across():
    q = query(block("construct A", "synonym A1", "synonym A2"), block("construct B", "synonym B1"), block("context term 1"))
    assert render(q) == (
        '("construct A" OR "synonym A1" OR "synonym A2") AND ("construct B" OR "synonym B1") AND ("context term 1")'
    )


def test_single_block_single_term():
    assert render(query(block("labour"))) == '("labour")'


def test_rendering_is_deterministic_and_order_preserving():
    a = query(block("b", "a"), block("d", "c"))
    assert render(a) == render(query(block("b", "a"), block("d", "c"))) == '("b" OR "a") AND ("d" OR "c")'


def test_whitespace_and_unicode_are_normalised():
    assert block("  female   labour\tforce ", "café").terms == ("female labour force", "café")


def test_duplicates_ignoring_case_are_removed_keeping_the_first():
    assert block("Women", "women", "WOMEN", "girls").terms == ("Women", "girls")


def test_non_ascii_terms_survive():
    assert render(query(block("महिला श्रम", "résumé"))) == '("महिला श्रम" OR "résumé")'


def test_wildcard_is_unquoted_single_word_truncation():
    assert render(query(block("employ*", "job search"))) == '(employ* OR "job search")'


@pytest.mark.parametrize("bad", ["*", "a*", "em*ploy", "*employ", "employ ment*", "employ**"])
def test_bad_wildcards_are_refused(bad):
    with pytest.raises(ValueError):
        normalise_term(bad)


@pytest.mark.parametrize(
    "bad",
    ['say "hi"', "a (b)", "a\b", "AND", "or", "Not", "NEAR", "", "   ", "a\u0000b", "zero​width", "tab\x07"],
)
def test_terms_that_could_change_the_query_are_refused_not_altered(bad):
    with pytest.raises(ValueError):
        normalise_term(bad)
    with pytest.raises(ValidationError):
        block(bad)


def test_operator_words_inside_a_phrase_are_fine():
    assert render(query(block("trade and growth", "cause or effect"))) == '("trade and growth" OR "cause or effect")'


def test_length_limits():
    assert normalise_term("x" * MAX_TERM_CHARS)
    with pytest.raises(ValueError):
        normalise_term("x" * (MAX_TERM_CHARS + 1))
    with pytest.raises(ValidationError):
        block(*[f"t{i}" for i in range(MAX_TERMS_PER_BLOCK + 1)])
    assert len(block(*[f"t{i}" for i in range(MAX_TERMS_PER_BLOCK)]).terms) == MAX_TERMS_PER_BLOCK
    with pytest.raises(ValidationError):
        query(*[block(f"t{i}") for i in range(MAX_BLOCKS + 1)])


def test_empty_block_or_query_is_refused():
    with pytest.raises(ValidationError):
        ConceptBlock(terms=())
    with pytest.raises(ValidationError):
        BooleanQuery(blocks=())


def test_a_query_too_long_for_the_connectors_is_refused_when_defined():
    big = [block(*[f"{'w' * 90}{i}" for i in range(MAX_TERMS_PER_BLOCK)]) for _ in range(3)]
    with pytest.raises(ValidationError, match=str(MAX_QUERY_CHARS)):
        query(*big)
    with pytest.raises(QueryError):
        render(BooleanQuery.model_construct(blocks=tuple(big)))


def test_unknown_fields_and_mutation_are_refused():
    with pytest.raises(ValidationError):
        ConceptBlock(terms=("a",), operator="NOT")
    q = query(block("a"))
    with pytest.raises(ValidationError):
        q.blocks = ()


def test_label_is_cosmetic_and_never_rendered():
    q = query(block("a", label="  Construct   A "), block("b", label=""))
    assert q.blocks[0].label == "Construct A" and q.blocks[1].label is None
    assert "Construct" not in render(q)


def test_hostile_text_never_reaches_the_structure():
    for hostile in ['x") OR ("y', "a) AND (b", 'a" OR "b']:
        with pytest.raises(ValidationError):
            block(hostile)
