from app.number_citation import RunFacts, check_section, flatten_numbers, resolve_number, stat_refs_in_order


def _stat(run_id: str, path: str) -> dict:
    return {"type": "stat", "attrs": {"analysis_run_id": run_id, "path": path}}


def _para(*children: dict) -> dict:
    return {"type": "paragraph", "content": list(children)}


def _doc(*paragraphs: dict) -> dict:
    return {"type": "doc", "content": list(paragraphs)}


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


OUTPUT = {
    "descriptives": {"age": {"n": 40, "mean": 35.5, "sd": 4.2, "min": 20, "max": 60}},
    "correlations": [{"a": "age", "b": "wage", "r": 0.31}],
    "seed": 0,
}
RUNS = {"r1": RunFacts(id="r1", output=OUTPUT)}


def _kinds(report) -> list[str]:
    return sorted(f.kind for f in report.findings)


def test_a_section_whose_inserted_numbers_all_resolve_has_no_findings():
    content = _doc(_para(_text("Mean age was "), _stat("r1", "descriptives.age.mean")))
    report = check_section(content, RUNS)
    assert report.findings == []
    assert report.refs == [("r1", "descriptives.age.mean")]


def test_a_number_naming_an_unknown_run_is_flagged():
    report = check_section(_doc(_para(_stat("nope", "seed"))), RUNS)
    assert _kinds(report) == ["unknown_run"]
    assert report.findings[0].subject == "nope"


def test_a_path_that_does_not_resolve_in_a_known_run_is_flagged():
    report = check_section(_doc(_para(_stat("r1", "descriptives.age.missing"))), RUNS)
    assert _kinds(report) == ["unresolved_number"]
    assert report.findings[0].subject == "r1:descriptives.age.missing"


def test_a_path_into_a_list_resolves_by_index():
    report = check_section(_doc(_para(_stat("r1", "correlations.0.r"))), RUNS)
    assert report.findings == []


def test_a_path_resolving_to_a_non_number_is_flagged():
    report = check_section(_doc(_para(_stat("r1", "correlations.0.a"))), RUNS)
    assert _kinds(report) == ["unresolved_number"]


def test_a_path_resolving_to_a_boolean_is_not_a_number():
    output = {"flag": True}
    report = check_section(_doc(_para(_stat("r1", "flag"))), {"r1": RunFacts(id="r1", output=output)})
    assert _kinds(report) == ["unresolved_number"]


def test_numbers_nested_inside_lists_are_found_once_each_in_order():
    content = _doc(
        {"type": "bullet_list", "content": [{"type": "list_item", "content": [_para(_stat("r1", "seed"))]}]},
        _para(_stat("r1", "seed"), _stat("r1", "descriptives.age.mean")),
    )
    report = check_section(content, RUNS)
    assert report.refs == [("r1", "seed"), ("r1", "descriptives.age.mean")]
    assert report.findings == []


def test_a_section_with_no_inserted_numbers_has_no_findings():
    report = check_section(_doc(), RUNS)
    assert report.findings == []
    assert report.ok


def test_resolve_number_rejects_missing_output_or_empty_path():
    assert resolve_number(None, "seed") is None
    assert resolve_number(OUTPUT, "") is None
    assert resolve_number({}, "seed") is None


def test_stat_refs_in_order_ignores_malformed_nodes():
    content = _doc(_para({"type": "stat", "attrs": {"analysis_run_id": "", "path": "seed"}}, _stat("r1", "seed")))
    assert stat_refs_in_order(content) == [("r1", "seed")]


def test_flatten_numbers_finds_every_real_number_leaf_by_dot_path():
    pairs = dict(flatten_numbers(OUTPUT))
    assert pairs["descriptives.age.n"] == 40
    assert pairs["descriptives.age.mean"] == 35.5
    assert pairs["correlations.0.r"] == 0.31
    assert pairs["seed"] == 0
    assert "correlations.0.a" not in pairs  # "wage" is a string, not a number


def test_flatten_numbers_skips_booleans():
    assert flatten_numbers({"flag": True, "n": 1}) == [("n", 1)]


def test_flatten_numbers_of_a_bare_number_returns_the_empty_path():
    assert flatten_numbers(5) == [("", 5)]
    assert flatten_numbers("not a number") == []
    assert flatten_numbers(None) == []


def test_every_path_flatten_numbers_returns_is_resolvable_by_resolve_number():
    for path, value in flatten_numbers(OUTPUT):
        assert resolve_number(OUTPUT, path) == value
