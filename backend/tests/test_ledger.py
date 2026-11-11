"""Pure tests for the authorship ledger logic (X.31.9): no database, no HTTP.

Documents are ProseMirror JSON as the editor sends it. Each text run carries one `prov` mark.
"""

from app import ledger


def run(text, sid, origin="typed", by="u1", sug=None, note=None, paste=None, src=None, marks=()):
    prov = {"sid": sid, "origin": origin, "by": by, "sug": sug, "note": note, "paste": paste, "src": src}
    return {"type": "text", "text": text, "marks": [{"type": "prov", "attrs": prov}, *({"type": m} for m in marks)]}


def cite(source_id):
    return {"type": "cite", "attrs": {"source_id": source_id}}


def stat(run_id, path):
    return {"type": "stat", "attrs": {"analysis_run_id": run_id, "path": path}}


def block(*inline, kind="paragraph"):
    return {"type": kind, "content": list(inline)}


def doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


EMPTY = doc()
NO_AI = {"accepted": {}, "suggestion_texts": [], "citable_source_ids": set()}


def kinds(changes):
    return sorted((c.kind, c.span_id) for c in changes)


def codes(problems):
    return [p.code for p in problems]


# --- diff_spans -----------------------------------------------------------------------------------

def test_new_span_is_inserted_with_its_length():
    changes = ledger.diff_spans(EMPTY, doc(block(run("Hello world.", "a"))))

    assert kinds(changes) == [("inserted", "a")]
    change = changes[0]
    assert (change.chars_before, change.chars_after) == (0, 12)
    assert change.text_hash == ledger.sha256_text("Hello world.")


def test_span_missing_from_the_new_version_is_removed():
    changes = ledger.diff_spans(doc(block(run("Gone.", "a"))), EMPTY)

    assert kinds(changes) == [("removed", "a")]
    assert (changes[0].chars_before, changes[0].chars_after) == (5, 0)


def test_changed_text_is_changed():
    changes = ledger.diff_spans(doc(block(run("Old.", "a"))), doc(block(run("New.", "a"))))

    assert kinds(changes) == [("changed", "a")]


def test_paste_status_change_alone_is_resolved():
    before = doc(block(run("Pasted text here.", "a", origin="pasted", paste="unconfirmed")))
    after = doc(block(run("Pasted text here.", "a", origin="pasted", paste="own")))

    assert kinds(ledger.diff_spans(before, after)) == [("resolved", "a")]


def test_unchanged_span_has_no_event():
    same = doc(block(run("Stable.", "a")))

    assert ledger.diff_spans(same, same) == []


def test_span_present_in_the_restored_version_is_restored():
    current = EMPTY
    target = doc(block(run("Back again.", "a")))

    changes = ledger.diff_spans(current, target, restored=target)

    assert kinds(changes) == [("restored", "a")]


# --- integrity checks -----------------------------------------------------------------------------

def test_existing_span_provenance_cannot_change():
    before = doc(block(run("Mine.", "a", origin="typed", by="u1")))
    after = doc(block(run("Mine.", "a", origin="dictated", by="u1")))

    problems = ledger.check_integrity(before, after, actor="u1", **NO_AI)

    assert codes(problems) == ["provenance_changed"]


def test_new_span_by_someone_else_is_refused_unless_restored():
    after = doc(block(run("Not mine.", "a", by="u2")))

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **NO_AI)) == ["foreign_author"]
    assert ledger.check_integrity(EMPTY, after, actor="u1", restored=after, **NO_AI) == []


def test_ai_text_without_an_accepted_suggestion_is_refused():
    after = doc(block(run("AI words.", "a", origin="ai_edited", sug="s1")))

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **NO_AI)) == ["ai_text_without_accept"]


def test_ai_text_must_match_the_accepted_proposal():
    after = doc(block(run("Something else.", "a", origin="ai_edited", sug="s1")))
    ai = {"accepted": {"s1": "The accepted sentence."}, "suggestion_texts": [], "citable_source_ids": set()}

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **ai)) == ["ai_text_mismatch"]


def test_below_full_a_new_ai_span_is_refused_but_a_saved_or_restored_one_is_kept():
    span = doc(block(run("The accepted sentence.", "a", origin="ai_suggested", sug="s1")))
    ai = {"accepted": {"s1": "The accepted sentence."}, "suggestion_texts": [], "citable_source_ids": set()}

    assert codes(ledger.check_integrity(EMPTY, span, actor="u1", ai_wording_allowed=False, **ai)) == ["ai_wording_not_allowed"]
    assert ledger.check_integrity(span, span, actor="u1", ai_wording_allowed=False, **ai) == []
    assert ledger.check_integrity(EMPTY, span, actor="u1", restored=span, ai_wording_allowed=False, **ai) == []


def test_accepted_ai_text_that_matches_is_allowed():
    after = doc(block(run("The accepted sentence.", "a", origin="ai_edited", sug="s1")))
    ai = {"accepted": {"s1": "The accepted sentence."}, "suggestion_texts": [], "citable_source_ids": set()}

    assert ledger.check_integrity(EMPTY, after, actor="u1", **ai) == []


def test_typed_text_that_copies_a_suggestion_is_refused_as_relabelled():
    suggestion = "Care burden falls disproportionately on women in most households."
    after = doc(block(run("Care burden falls disproportionately on women in most households.", "a", by="u1")))
    ai = {"accepted": {}, "suggestion_texts": [suggestion], "citable_source_ids": set()}

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **ai)) == ["ai_text_relabelled"]


def test_short_overlap_with_a_suggestion_is_allowed():
    after = doc(block(run("Care burden is shared.", "a", by="u1")))
    ai = {"accepted": {}, "suggestion_texts": ["Care burden falls disproportionately on women."], "citable_source_ids": set()}

    assert ledger.check_integrity(EMPTY, after, actor="u1", **ai) == []


def test_quotation_needs_a_citation_in_the_same_paragraph():
    quoted = run("A quoted sentence.", "a", origin="pasted", paste="quotation")
    citable = {"accepted": {}, "suggestion_texts": [], "citable_source_ids": {"s1"}}

    assert codes(ledger.check_integrity(EMPTY, doc(block(quoted)), actor="u1", **citable)) == ["quotation_without_citation"]
    assert codes(ledger.check_integrity(EMPTY, doc(block(quoted), block(cite("s1"))), actor="u1", **citable)) == [
        "quotation_without_citation"
    ]
    assert ledger.check_integrity(EMPTY, doc(block(quoted, cite("s1"))), actor="u1", **citable) == []


def test_new_citation_must_point_at_a_citable_source():
    after = doc(block(run("Claim.", "a"), cite("s-unknown")))
    ai = {"accepted": {}, "suggestion_texts": [], "citable_source_ids": {"s1"}}

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **ai)) == ["uncited_source"]


def test_existing_citation_is_kept_even_if_its_source_is_no_longer_citable():
    before = doc(block(run("Claim.", "a"), cite("s1")))
    after = doc(block(run("Claim.", "a"), cite("s1"), run(" More.", "b")))

    assert ledger.check_integrity(before, after, actor="u1", accepted={}, suggestion_texts=[], citable_source_ids=set()) == []


def test_new_number_must_resolve_against_a_known_analysis_run():
    after = doc(block(run("Mean age was ", "a"), stat("run1", "mean")))

    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", **NO_AI)) == ["unresolved_number"]
    assert codes(ledger.check_integrity(EMPTY, after, actor="u1", number_run_outputs={"run1": {"mean": 35.5}}, **NO_AI)) == []


def test_new_number_whose_path_does_not_resolve_is_refused_even_with_a_known_run():
    after = doc(block(stat("run1", "missing")))

    problems = ledger.check_integrity(EMPTY, after, actor="u1", number_run_outputs={"run1": {"mean": 35.5}}, **NO_AI)

    assert codes(problems) == ["unresolved_number"]


def test_existing_number_is_kept_even_if_its_run_is_no_longer_known():
    before = doc(block(stat("run1", "mean")))
    after = doc(block(stat("run1", "mean"), run(" More.", "b")))

    assert ledger.check_integrity(before, after, actor="u1", **NO_AI) == []



# --- schema, size and text ------------------------------------------------------------------------

def test_disallowed_node_is_an_invalid_schema():
    bad = doc({"type": "image", "attrs": {"src": "x"}})

    assert codes(ledger.validate_schema(bad)) == ["invalid_schema"]


def test_text_without_exactly_one_prov_mark_is_refused():
    bare = doc(block({"type": "text", "text": "No mark."}))

    assert codes(ledger.validate_schema(bare)) == ["missing_provenance"]


def test_unknown_origin_is_an_invalid_schema():
    bad = doc(block(run("Text.", "a", origin="magic")))

    assert codes(ledger.validate_schema(bad)) == ["invalid_schema"]


def test_a_stat_node_is_allowed_and_needs_its_attrs():
    good = doc(block(stat("run1", "mean")))
    missing_path = doc({"type": "stat", "attrs": {"analysis_run_id": "run1"}})

    assert ledger.validate_schema(good) == []
    assert codes(ledger.validate_schema(missing_path)) == ["invalid_schema"]


def test_text_over_the_limit_is_refused():
    big = doc(block(run("x" * (ledger.MAX_CONTENT_TEXT + 1), "a")))

    assert codes(ledger.validate_schema(big)) == ["text_too_long"]


def test_plain_text_puts_each_block_on_its_own_line():
    text = ledger.plain_text(doc(block(run("First.", "a")), block(run("Second.", "b"), kind="heading")))

    assert text == "First.\nSecond."


def test_content_hash_ignores_key_order():
    first = {"type": "doc", "content": []}
    second = {"content": [], "type": "doc"}

    assert ledger.content_hash(first) == ledger.content_hash(second)


# --- paste source detection -----------------------------------------------------------------------

SOURCE_TEXT = "Women carry most unpaid care work, and this shapes who can  work full time."


def test_pasted_text_matching_a_source_is_detected_ignoring_case_and_spacing():
    pasted = doc(block(run("women carry most unpaid care work, and this shapes who can work full time.", "p", origin="pasted")))
    candidates = [("source", "src-1", SOURCE_TEXT)]

    assert ledger.detect_pasted(EMPTY, pasted, candidates) == {"p": ("source", "src-1")}


def test_short_pasted_text_is_not_matched():
    pasted = doc(block(run("Short quote.", "p", origin="pasted")))

    assert ledger.detect_pasted(EMPTY, pasted, [("source", "src-1", "Short quote. Plus more.")]) == {}


def test_only_new_pasted_spans_are_checked():
    already = doc(block(run("women carry most unpaid care work, and this shapes who can work full time.", "p", origin="pasted")))

    assert ledger.detect_pasted(already, already, [("source", "src-1", SOURCE_TEXT)]) == {}


def test_typed_text_is_never_matched_as_a_paste():
    typed = doc(block(run("women carry most unpaid care work, and this shapes who can work full time.", "p", origin="typed")))

    assert ledger.detect_pasted(EMPTY, typed, [("source", "src-1", SOURCE_TEXT)]) == {}


def test_a_client_sent_stat_value_is_replaced_by_the_number_the_run_holds():
    outputs = {"run1": {"results": {"pearson_r": 0.41}}}
    forged = doc(block({"type": "stat", "attrs": {"analysis_run_id": "run1", "path": "results.pearson_r", "value": 0.99}}))

    pinned = ledger.pin_stat_values(forged, outputs)

    assert pinned["content"][0]["content"][0]["attrs"]["value"] == 0.41
    assert forged["content"][0]["content"][0]["attrs"]["value"] == 0.99  # the input is not mutated


def test_a_stat_value_for_a_path_that_does_not_resolve_is_dropped_and_then_refused():
    outputs = {"run1": {"results": {"pearson_r": 0.41}}}
    forged = doc(block({"type": "stat", "attrs": {"analysis_run_id": "run1", "path": "results.nope", "value": 0.99}}))

    pinned = ledger.pin_stat_values(forged, outputs)

    assert "value" not in pinned["content"][0]["content"][0]["attrs"]
    assert codes(ledger.check_integrity(EMPTY, pinned, actor="u1", number_run_outputs=outputs, **NO_AI)) == ["unresolved_number"]
