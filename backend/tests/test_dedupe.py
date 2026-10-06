import pytest

from app.connectors.base import PaperRecord
from app.dedupe import (
    MIN_TITLE_CHARS,
    DedupeRecord,
    doi_key,
    first_author_key,
    group_duplicates,
    match_kind,
    normalise_title,
    work_key,
)

T = "Female labour force participation in India"


def rec(title=T, doi=None, year=2020, authors=("Ada Lovelace",)):
    return DedupeRecord(title=title, doi=doi, year=year, authors=authors)


# --- title normalisation (M1.6.2) ---------------------------------------------


def test_case_punctuation_and_spacing_are_ignored():
    assert normalise_title("  Women's  Work:  A Review! ") == normalise_title("women s work a review")
    assert normalise_title("Trade-offs (and more)") == "trade offs and more"


def test_html_from_publisher_feeds_is_removed():
    assert normalise_title("<i>In vitro</i> studies of &amp; models") == normalise_title("In vitro studies of & models")
    assert normalise_title("A <b>bold</b> claim") == "a bold claim"


def test_non_ascii_letters_are_kept_not_dropped():
    assert normalise_title("महिला श्रम बल भागीदारी") == "महिला श्रम बल भागीदारी"  # Devanagari incl. vowel signs
    assert normalise_title("女性劳动力参与率研究") == "女性劳动力参与率研究"
    assert normalise_title("Исследование занятости") == "исследование занятости"
    assert normalise_title("مشاركة المرأة") == "مشاركة المرأة"


def test_unrelated_non_latin_titles_do_not_collide():
    assert normalise_title("महिला श्रम बल") != normalise_title("किसान आत्महत्या")
    assert work_key(rec("女性劳动力参与率研究", authors=("王",))) != work_key(rec("农村教育投入的影响分析", authors=("王",)))


def test_latin_accents_fold_but_other_scripts_keep_their_marks():
    assert normalise_title("Café résumé naïve") == normalise_title("Cafe resume naive")
    assert normalise_title("Müller & Łukasiewicz") == normalise_title("Muller & Łukasiewicz")  # Ł has no combining mark
    assert normalise_title("किताब") != normalise_title("कतब")  # vowel signs matter in Devanagari


def test_compatibility_forms_unify():
    assert normalise_title("ﬁnancial ＡＢＣ") == normalise_title("financial abc")  # ligature, fullwidth
    assert normalise_title("Café") == normalise_title("Café")  # composed vs decomposed


def test_dense_scripts_need_fewer_characters_to_identify_a_work():
    assert work_key(rec("女性劳动力参与率研究", authors=("王",))) is not None  # 10 characters, weight 20
    assert work_key(rec("女性劳动", authors=("王",))) is None  # 4 characters, weight 8


def test_digits_stay_and_empty_inputs_are_safe():
    assert normalise_title("COVID-19 in 2020") == "covid 19 in 2020"
    for empty in (None, "", "   ", "!!!", 5):
        assert normalise_title(empty) == ""


# --- first author --------------------------------------------------------------


@pytest.mark.parametrize(
    "authors,expected",
    [
        (["Lovelace, Ada"], "lovelace"),
        (["Ada Lovelace"], "lovelace"),
        (["Ada  K.  Lovelace"], "lovelace"),
        (["Ludwig van Beethoven"], "beethoven"),
        (["Beethoven, Ludwig van"], "beethoven"),
        (["Garcia Lopez, Maria"], "lopez"),
        (["Maria Garcia Lopez"], "lopez"),
        (["van der Berg, Jan"], "berg"),
        (["Jan van der Berg"], "berg"),
        (["José Álvarez"], "alvarez"),
        (["王小明"], "王小明"),
        (["Madonna"], "madonna"),
        (["", "  ", "Second Author"], "author"),
        ([], ""),
        (None, ""),
    ],
)
def test_first_author_key(authors, expected):
    assert first_author_key(authors) == expected


def test_only_the_first_author_counts():
    assert first_author_key(["Ada Lovelace", "Charles Babbage"]) == first_author_key(["A. Lovelace"])


# --- keys and matching ---------------------------------------------------------


def test_doi_key_normalises_or_gives_none():
    assert doi_key("https://doi.org/10.1000/ABC") == doi_key("doi:10.1000/abc") == "10.1000/abc"
    assert doi_key("garbage") is None and doi_key(None) is None and doi_key(5) is None


def test_same_doi_matches_whatever_else_differs():
    a, b = rec(doi="10.1000/ABC", year=2019), rec(title="Totally different title text", doi="https://doi.org/10.1000/abc", year=2020)
    assert match_kind(a, b) == "doi"


def test_different_dois_never_match_even_with_identical_work_key():
    assert match_kind(rec(doi="10.1000/preprint"), rec(doi="10.1000/published")) is None


def test_title_year_author_matches_when_a_doi_is_missing_on_one_side():
    assert match_kind(rec(doi="10.1000/x"), rec(title=T.upper() + ".", doi=None, authors=("Lovelace, A.",))) == "title_year_author"
    assert match_kind(rec(), rec(authors=("Lovelace, Augusta",))) == "title_year_author"


@pytest.mark.parametrize(
    "other",
    [
        rec(year=2021),
        rec(authors=("Charles Babbage",)),
        rec(title="A different paper about something else entirely"),
        rec(year=None),  # a missing year only matches a missing year
        rec(authors=()),
    ],
)
def test_any_differing_component_prevents_a_title_match(other):
    assert match_kind(rec(), other) is None


def test_missing_parts_match_each_other():
    assert match_kind(rec(year=None, authors=None), rec(year=None, authors=())) == "title_year_author"


def test_short_titles_have_no_work_key_and_never_match():
    assert len(normalise_title("Introduction")) < MIN_TITLE_CHARS
    assert work_key(rec("Introduction")) is None
    assert work_key(rec("Women and work")) is None  # 14 characters
    assert work_key(rec("Women and work!!")) is None  # punctuation doesn't pad it
    assert match_kind(rec("Introduction"), rec("Introduction")) is None
    assert match_kind(rec("Introduction", doi="10.1000/a"), rec("Introduction", doi="10.1000/a")) == "doi"


def test_works_on_paper_records_too():
    a = PaperRecord(connector="openalex", external_id="W1", title=T, doi="10.1000/ABC", year=2020, authors=("Ada Lovelace",))
    b = PaperRecord(connector="crossref", external_id="10.1000/abc", title=T, doi="10.1000/abc", year=2020, authors=("Ada Lovelace",))
    assert match_kind(a, b) == "doi"
    assert group_duplicates([a, b]) == [[0, 1]]


# --- grouping --------------------------------------------------------------------


def test_groups_keep_input_order_and_indices():
    records = [rec(doi="10.1000/a"), rec("Another distinct paper title here", year=2018), rec(doi="https://doi.org/10.1000/A", title="x"), rec("another DISTINCT paper title here!", year=2018, authors=("A. Lovelace",))]
    assert group_duplicates(records) == [[0, 2], [1, 3]]


def test_unique_records_are_singletons_and_empty_input_is_empty():
    assert group_duplicates([]) == []
    assert group_duplicates([rec(title=f"Unrelated paper number {n} about topic {n}") for n in range(3)]) == [[0], [1], [2]]


def test_a_chain_cannot_merge_papers_with_conflicting_dois():
    a = rec(doi="10.1000/preprint")
    b = rec(doi=None)  # matches a by title, no DOI of its own
    c = rec(doi="10.1000/published")  # same work key, different DOI from a
    groups = group_duplicates([a, b, c])
    assert groups == [[0, 1], [2]]
    # The conflict is order-independent: c can't be pulled in via b either.
    assert group_duplicates([c, b, a]) == [[0, 1], [2]]


def test_a_doi_less_record_joins_the_first_compatible_group():
    assert group_duplicates([rec(doi="10.1000/a"), rec(doi=None), rec(doi="10.1000/a")]) == [[0, 1, 2]]


def test_same_title_different_years_stay_apart():
    assert group_duplicates([rec(year=2019), rec(year=2020)]) == [[0], [1]]


def test_malformed_dois_are_ignored_not_fatal():
    assert group_duplicates([rec(doi="garbage"), rec(doi="more garbage")]) == [[0, 1]]  # fall back to the work key
