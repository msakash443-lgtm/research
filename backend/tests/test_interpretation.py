from app.interpretation import (
    Agreement,
    AnalysisFinding,
    InterpretationItem,
    LiteratureValue,
    build_map,
    classify,
    draft_content,
)


def _lit(source_id: str, extraction_id: str, field_key: str, value: float) -> LiteratureValue:
    return LiteratureValue(source_id=source_id, extraction_id=extraction_id, field_key=field_key, value=value)


def _finding(output_key: str = "pearson_r", value: float | None = 0.4, path: str = "results.pearson_r") -> AnalysisFinding:
    return AnalysisFinding(run_id="run1", output_key=output_key, path=path, value=value)


def test_same_sign_literature_values_agree():
    literature = [_lit("s1", "e1", "pearson_r", 0.3), _lit("s2", "e2", "pearson_r", 0.5)]
    item = classify(_finding(value=0.4), literature)
    assert item.classification is Agreement.agree
    assert item.literature_source_ids == ("s1", "s2")
    assert item.literature_extraction_ids == ("e1", "e2")


def test_opposite_sign_literature_values_disagree():
    literature = [_lit("s1", "e1", "pearson_r", -0.3), _lit("s2", "e2", "pearson_r", -0.2)]
    item = classify(_finding(value=0.4), literature)
    assert item.classification is Agreement.disagree


def test_mixed_sign_literature_values_are_unclear():
    literature = [_lit("s1", "e1", "pearson_r", -0.3), _lit("s2", "e2", "pearson_r", 0.2)]
    item = classify(_finding(value=0.4), literature)
    assert item.classification is Agreement.unclear
    assert "disagree with each other" in item.rationale


def test_no_matching_literature_value_is_unclear_and_cites_nothing():
    item = classify(_finding(output_key="novel_metric"), [_lit("s1", "e1", "pearson_r", 0.3)])
    assert item.classification is Agreement.unclear
    assert item.literature_source_ids == ()
    assert "No prior literature value" in item.rationale


def test_a_missing_finding_value_is_unclear_even_with_matching_literature():
    item = classify(_finding(value=None), [_lit("s1", "e1", "pearson_r", 0.3)])
    assert item.classification is Agreement.unclear
    assert "no numeric value" in item.rationale


def test_zero_valued_finding_is_unclear():
    item = classify(_finding(value=0.0), [_lit("s1", "e1", "pearson_r", 0.3)])
    assert item.classification is Agreement.unclear


def test_zero_valued_literature_is_unclear():
    item = classify(_finding(value=0.4), [_lit("s1", "e1", "pearson_r", 0.0)])
    assert item.classification is Agreement.unclear


def test_build_map_classifies_each_finding_independently_against_the_same_literature():
    literature = [_lit("s1", "e1", "pearson_r", 0.3), _lit("s1", "e1", "effect_size", -1.0)]
    findings = [_finding(output_key="pearson_r", value=0.5), _finding(output_key="effect_size", value=0.5, path="results.effect_size")]
    items = build_map(findings, literature)
    assert [item.classification for item in items] == [Agreement.agree, Agreement.disagree]


def test_draft_content_builds_one_paragraph_per_item_with_stat_and_cite_nodes_only():
    item = InterpretationItem(
        finding=_finding(value=0.4), classification=Agreement.agree,
        literature_source_ids=("s1", "s2"), literature_extraction_ids=("e1", "e2"), rationale="matches prior work",
    )
    doc = draft_content([item])
    assert doc["type"] == "doc"
    paragraph = doc["content"][0]
    assert paragraph["type"] == "paragraph"
    types = [node["type"] for node in paragraph["content"]]
    assert types.count("stat") == 1
    assert types.count("cite") == 2
    stat_node = next(node for node in paragraph["content"] if node["type"] == "stat")
    assert stat_node["attrs"] == {"analysis_run_id": "run1", "path": "results.pearson_r"}
    cite_source_ids = {node["attrs"]["source_id"] for node in paragraph["content"] if node["type"] == "cite"}
    assert cite_source_ids == {"s1", "s2"}
    # No free-prose numbers: every digit in the paragraph's plain text comes only from the rationale meta-text,
    # never a substituted finding/literature value.
    text_nodes = " ".join(node["text"] for node in paragraph["content"] if node["type"] == "text")
    assert "0.4" not in text_nodes


def test_draft_content_with_no_items_returns_an_empty_paragraph():
    doc = draft_content([])
    assert doc == {"type": "doc", "content": [{"type": "paragraph", "content": []}]}
