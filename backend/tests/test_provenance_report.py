"""Writing provenance report, pure logic (X.31.12).

Every number in the report is computed here from character counts and timestamps; the statement is filled
from a pinned template file. No model is called, and no span text reaches the report.
"""

from datetime import datetime, timedelta, timezone

from app import provenance_report as pr


def _span(document_id="d1", span_id="s", chars=10, origin="typed", paste=None, suggestion_id=None,
          model_id=None, prompt_version=None, action=None, source_id=None):
    return {
        "document_id": document_id, "span_id": span_id, "chars": chars, "origin": origin, "paste": paste,
        "suggestion_id": suggestion_id, "model_id": model_id, "prompt_version": prompt_version,
        "action": action, "source_id": source_id,
    }


def _version(session, minutes, document_id="d1"):
    base = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    return {"document_id": document_id, "client_session_id": session, "created_at": base + timedelta(minutes=minutes)}


def test_shares_come_from_character_counts():
    spans = [
        _span(span_id="a", chars=60),
        _span(span_id="b", chars=20, origin="pasted", paste="quotation", source_id="src-1"),
        _span(span_id="c", chars=10, origin="pasted", paste="own"),
        _span(span_id="d", chars=5, origin="pasted", paste="unconfirmed"),
        _span(span_id="e", chars=5, origin="ai_edited", suggestion_id="g1", model_id="m", prompt_version="p", action="challenge"),
    ]
    report = pr.build_report("manuscript", [], spans, [])

    assert report["total_chars"] == 100
    assert report["shares"] == {"typed": 60.0, "quoted": 20.0, "pasted_own": 10.0, "pasted_unresolved": 5.0, "ai": 5.0}


def test_no_text_gives_zero_shares_and_no_ai():
    report = pr.build_report("section", [], [], [])

    assert report["total_chars"] == 0
    assert set(report["shares"].values()) == {0.0}
    assert report["ai"]["items"] == []


def test_ai_items_list_model_action_and_chars_kept():
    spans = [
        _span(span_id="a", chars=30, origin="ai_edited", suggestion_id="g1", model_id="model-x",
              prompt_version="workspace_challenge.v1", action="challenge"),
        _span(span_id="b", chars=10, origin="ai_edited", suggestion_id="g1", model_id="model-x",
              prompt_version="workspace_challenge.v1", action="challenge"),
        _span(span_id="c", chars=10, origin="ai_suggested", suggestion_id="g2", model_id="model-y",
              prompt_version="workspace_clearer_sentence.v1", action="clearer_sentence"),
        _span(span_id="d", chars=50, origin="typed"),
    ]
    report = pr.build_report("manuscript", [], spans, [])

    assert report["ai"]["chars"] == 50
    assert report["ai"]["share_pct"] == 50.0
    items = {item["suggestion_id"]: item for item in report["ai"]["items"]}
    assert items["g1"] == {
        "suggestion_id": "g1", "action": "challenge", "model_id": "model-x",
        "prompt_version": "workspace_challenge.v1", "chars_kept": 40,
    }
    assert items["g2"]["chars_kept"] == 10


def test_sessions_are_counted_by_client_session_with_hours():
    versions = [
        _version("A", 0), _version("A", 45), _version("A", 90),  # 90 minutes in session A
        _version("B", 240),                                      # a single save: no time
    ]
    report = pr.build_report("manuscript", [], [], versions)

    assert report["sessions"] == {"count": 2, "hours": 1.5, "versions_saved": 4}


def test_pasted_items_carry_status_and_source_but_no_text():
    spans = [_span(span_id="p1", chars=25, origin="pasted", paste="quotation", source_id="src-1")]
    report = pr.build_report("manuscript", [], spans, [])

    assert report["pasted"] == [{"document_id": "d1", "span_id": "p1", "status": "quotation", "source_id": "src-1", "chars": 25}]


def test_statement_with_ai_names_models_and_actions():
    spans = [_span(span_id="a", chars=20, origin="ai_edited", suggestion_id="g1", model_id="model-x",
                   prompt_version="workspace_challenge.v1", action="challenge")]
    report = pr.build_report("manuscript", [], spans, [])
    template = pr.load_template()

    statement = pr.render_statement(report, template)

    assert "model-x" in statement
    assert "Challenge my argument" in statement
    assert "100.0%" in statement  # every character of this text is AI-origin


def test_statement_without_ai_is_the_no_ai_variant():
    report = pr.build_report("manuscript", [], [_span(chars=40)], [])
    template = pr.load_template()

    assert pr.render_statement(report, template) == template["without_ai"]


def test_template_is_pinned_and_versioned():
    template = pr.load_template()

    assert template["id"] == "ai_use_statement"
    assert template["version"] == 2
    assert pr.template_label(template) == "ai_use_statement@2"


def test_hash_is_stable_and_changes_with_content():
    spans = [_span(chars=10)]
    report = pr.build_report("section", [], spans, [])
    statement = pr.render_statement(report, pr.load_template())

    assert pr.report_hash(report, statement) == pr.report_hash(report, statement)
    other = pr.build_report("section", [], [_span(chars=10, origin="pasted", paste="own")], [])
    assert pr.report_hash(other, pr.render_statement(other, pr.load_template())) != pr.report_hash(report, statement)


def test_markdown_has_statement_and_numbers():
    report = pr.build_report("section", [], [_span(chars=10)], [])
    statement = pr.render_statement(report, pr.load_template())

    markdown = pr.to_markdown(report, statement, "ai_use_statement@1")

    assert markdown.startswith("# Writing provenance report")
    assert statement in markdown
    assert "Typed by authors" in markdown
    assert "ai_use_statement@1" in markdown


def test_statements_claim_only_what_the_workspace_record_shows():
    template = pr.load_template()
    ai_report = pr.build_report("manuscript", [], [_span(origin="ai_edited", chars=20, suggestion_id="g1", model_id="m", action="challenge")], [])
    texts = [pr.render_statement(pr.build_report("manuscript", [], [_span(chars=40)], []), template),
             pr.render_statement(ai_report, template)]

    assert "No text in this manuscript was generated" not in texts[0]
    assert "workspace" in texts[0] and "record" in texts[0]
    assert "reviewed all AI-assisted text" not in texts[1]  # the system never checks that the author did


def test_markdown_says_not_recorded_instead_of_none():
    spans = [_span(origin="ai_edited", chars=10), _span(span_id="p", origin="pasted", chars=5)]
    report = pr.build_report("section", [], spans, [])

    markdown = pr.to_markdown(report, pr.render_statement(report, pr.load_template()), "ai_use_statement@2")

    assert "None" not in markdown
    assert "not recorded" in markdown
    assert "no source linked" in markdown
