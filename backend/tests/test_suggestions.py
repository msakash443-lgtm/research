import pytest

from app import suggestions as s


def test_a_new_suggestion_is_pending_and_counts_as_no_one_s_text():
    assert s.state_of([]) == s.PENDING_STATE
    assert s.accepted_text("AI wording", []) is None


def test_accepting_as_is_makes_the_ai_text_the_accepted_text():
    events = [s.Event(s.ACCEPTED)]
    assert s.state_of(e.kind for e in events) == s.ACCEPTED_STATE
    assert s.accepted_text("AI wording", events) == "AI wording"


def test_editing_before_accepting_keeps_the_researchers_wording_not_the_ais():
    events = [s.Event(s.EDITED_AND_ACCEPTED, "My wording")]
    assert s.accepted_text("AI wording", events) == "My wording"


def test_undoing_an_accept_returns_it_to_pending_with_no_accepted_text():
    events = [s.Event(s.ACCEPTED), s.Event(s.UNDONE)]
    assert s.state_of(e.kind for e in events) == s.PENDING_STATE
    assert s.accepted_text("AI wording", events) is None


def test_after_undo_the_latest_accept_wins_over_the_earlier_one():
    events = [s.Event(s.EDITED_AND_ACCEPTED, "First"), s.Event(s.UNDONE), s.Event(s.ACCEPTED)]
    assert s.accepted_text("AI wording", events) == "AI wording"


def test_a_discarded_suggestion_can_be_restored_but_not_accepted_directly():
    assert s.state_of([s.DISCARDED, s.RESTORED]) == s.PENDING_STATE
    with pytest.raises(s.InvalidTransition):
        s.state_of([s.DISCARDED, s.ACCEPTED])


@pytest.mark.parametrize(
    "history,event",
    [
        ([s.ACCEPTED], s.DISCARDED),  # undo first
        ([s.ACCEPTED], s.ACCEPTED),
        ([], s.UNDONE),
        ([], s.RESTORED),
        ([s.REASKED], s.ACCEPTED),  # replaced by a newer answer
        ([s.LOCKED], s.RESTORED),  # "never for this item" is not reversible here
        ([s.ACCEPTED], s.LOCKED),
    ],
)
def test_moves_the_state_machine_must_refuse(history, event):
    with pytest.raises(s.InvalidTransition):
        s.state_of([*history, event])


@pytest.mark.parametrize("kind", ["gate", "Gate", " GATE_DECISION ", "gate_g3", "approval", "stage"])
def test_an_ai_suggestion_can_never_target_a_gate_or_the_stage(kind):
    with pytest.raises(s.ForbiddenTarget):
        s.validate_target(kind)


def test_an_ordinary_target_kind_is_normalised():
    assert s.validate_target(" Note ") == "note"


def test_an_edit_must_have_text():
    with pytest.raises(ValueError):
        s.validate_final_text("   ")
    assert s.validate_final_text("  ok ") == "ok"


def test_confidence_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError):
        s.validate_confidence(1.2)
    assert s.validate_confidence(None) is None


def test_bulk_touches_only_pending_suggestions_that_have_a_confidence():
    items = [
        ("hi", s.PENDING_STATE, 0.9),
        ("mid", s.PENDING_STATE, 0.5),
        ("lo", s.PENDING_STATE, 0.1),
        ("none", s.PENDING_STATE, None),
        ("done", s.ACCEPTED_STATE, 0.99),
        ("gone", s.DISCARDED_STATE, 0.01),
    ]
    plan = s.plan_bulk(items, accept_at_or_above=0.8, discard_below=0.3)
    assert plan.accept == ["hi"]
    assert plan.discard == ["lo"]
    assert sorted(plan.skipped) == ["done", "gone", "mid", "none"]


def test_bulk_accept_threshold_is_inclusive_and_discard_threshold_is_exclusive():
    items = [("a", s.PENDING_STATE, 0.8), ("b", s.PENDING_STATE, 0.3)]
    plan = s.plan_bulk(items, accept_at_or_above=0.8, discard_below=0.3)
    assert plan.accept == ["a"] and plan.discard == [] and plan.skipped == ["b"]


def test_bulk_with_overlapping_thresholds_or_none_is_refused():
    with pytest.raises(ValueError):
        s.plan_bulk([], accept_at_or_above=0.3, discard_below=0.8)
    with pytest.raises(ValueError):
        s.plan_bulk([], accept_at_or_above=None, discard_below=None)
