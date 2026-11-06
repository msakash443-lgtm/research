"""Plan M3.1: extraction schemas are validated config; every non-null field needs evidence."""

import pytest

from app import extraction_schema as xs


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(xs, "SCHEMA_DIR", tmp_path)
    xs._load.cache_clear()
    yield tmp_path
    xs._load.cache_clear()


def test_default_schema_matches_appendix_b():
    schema = xs.load_schema()
    assert xs.schema_version(schema) == "default@1"
    assert [f.key for f in schema.fields] == [
        "research_question", "theory_used", "constructs", "measures", "sample", "method",
        "key_findings", "limitations", "future_research",
    ]
    assert {f.key for f in schema.fields if f.critical} == {"measures", "sample", "key_findings"}


def test_unknown_and_malformed_schemas_fail_loudly(schema_dir):
    (schema_dir / "bad.json").write_text("{not json", encoding="utf-8")
    (schema_dir / "dup.json").write_text(
        '{"name":"dup","version":1,"label":"d","description":"d","fields":'
        '[{"key":"aa","type":"string","description":"x"},{"key":"aa","type":"string","description":"y"}]}', encoding="utf-8")
    (schema_dir / "typo.json").write_text(
        '{"name":"typo","version":1,"label":"d","description":"d","fields":[{"key":"aa","type":"strng","description":"x"}]}',
        encoding="utf-8")
    for name in ("bad", "dup", "typo", "missing", "../x"):
        with pytest.raises(xs.SchemaError):
            xs.load_schema(name)


GOOD_SPAN = {"quote": "We surveyed 212 nurses.", "page": 4, "section": "Methods"}


def test_well_formed_extraction_passes():
    schema = xs.load_schema()
    fields = {"sample": {"n": 212, "population": "nurses", "country": "UK"}, "limitations": None}
    assert xs.check_extraction(schema, fields, {"sample": GOOD_SPAN}) == []


@pytest.mark.parametrize("page", ["", "   ", {}, [], True, False, 0, -1, 1.5])
def test_invalid_page_locators_are_rejected(page):
    schema = xs.load_schema()
    span = {"quote": "A quoted passage", "page": page}
    assert xs.check_extraction(schema, {"research_question": "Q?"}, {"research_question": span}) == [
        "research_question: page must be a positive number or a non-empty printed label",
    ]


@pytest.mark.parametrize("page", [1, "iv", " 123 "])
def test_positive_page_numbers_and_nonblank_printed_labels_are_accepted(page):
    schema = xs.load_schema()
    span = {"quote": "A quoted passage", "page": page}
    assert xs.check_extraction(schema, {"research_question": "Q?"}, {"research_question": span}) == []


def test_section_is_sufficient_when_page_is_absent():
    schema = xs.load_schema()
    span = {"quote": "A quoted passage", "section": "Methods"}
    assert xs.check_extraction(schema, {"research_question": "Q?"}, {"research_question": span}) == []


def test_non_null_field_without_evidence_is_rejected():
    schema = xs.load_schema()
    problems = xs.check_extraction(schema, {"research_question": "Does X cause Y?"}, {})
    assert problems == ["research_question: a non-null field needs evidence {quote, page, section}"]


@pytest.mark.parametrize("span", [
    {"quote": "", "page": 1}, {"quote": "   ", "section": "Intro"}, {"quote": "q"}, {"quote": "q", "section": " "}, {"page": 2},
])
def test_incomplete_evidence_is_rejected(span):
    schema = xs.load_schema()
    assert xs.check_extraction(schema, {"research_question": "Q?"}, {"research_question": span})


def test_wrong_type_unknown_field_and_orphan_evidence():
    schema = xs.load_schema()
    problems = xs.check_extraction(
        schema, {"sample": "212", "invented": 1, "theory_used": True}, {"sample": GOOD_SPAN, "theory_used": GOOD_SPAN, "method": GOOD_SPAN},
    )
    assert "sample: expected object" in problems
    assert "theory_used: expected list" in problems
    assert any(p.startswith("invented:") for p in problems)
    assert "method: evidence given for an empty field" in problems


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("sample", {"n": "many", "population": [], "country": {}}, [
            "sample.country: expected string",
            "sample.n: expected integer",
            "sample.population: expected string",
        ]),
        ("sample", {}, [
            "sample.country: missing property",
            "sample.n: missing property",
            "sample.population: missing property",
        ]),
        ("measures", [{"construct": "trust", "scale": "X", "reliability": "high"}], [
            "measures[0].reliability: expected number",
        ]),
        ("measures", [{"construct": "trust", "scale": "X"}], [
            "measures[0].reliability: missing property",
        ]),
        ("constructs", [{"name": "trust", "definition": "belief", "extra": True}], [
            "constructs[0].extra: unexpected property",
        ]),
        ("theory_used", ["resource dependence", 42], [
            "theory_used[1]: expected string",
        ]),
    ],
)
def test_nested_values_are_checked_against_the_appendix_b_shape(key, value, expected):
    schema = xs.load_schema()
    assert xs.check_extraction(schema, {key: value}, {key: GOOD_SPAN}) == expected


def test_appendix_b_nested_values_allow_declared_nulls():
    schema = xs.load_schema()
    fields = {
        "measures": [{"construct": "trust", "scale": "X", "reliability": None}],
        "key_findings": [{"statement": "No effect", "effect_size": None, "p_or_ci": None}],
    }
    evidence = {"measures": GOOD_SPAN, "key_findings": GOOD_SPAN}
    assert xs.check_extraction(schema, fields, evidence) == []
