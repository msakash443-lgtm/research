"""Pure rules for section drafting (X.46.8 / M6.1.a): outline fencing, reply validation, leak check."""

import pytest

from app import section_draft
from app.section_draft import OutlineClaim, SectionDraftError


def _claims(n=3):
    return [OutlineClaim(claim_id=f"c{i}", text=f"Claim number {i}.", verified=(i % 2 == 0)) for i in range(n)]


def test_an_empty_outline_is_refused_loudly():
    with pytest.raises(SectionDraftError) as exc:
        section_draft.ensure_outline([])
    assert exc.value.code == "empty_outline"


def test_outline_is_capped_at_max_claims():
    claims = _claims(section_draft.MAX_CLAIMS + 5)
    sent = section_draft.ensure_outline(claims)
    assert len(sent) == section_draft.MAX_CLAIMS
    assert sent == claims[: section_draft.MAX_CLAIMS]


def test_outline_is_fenced_with_a_nonce_and_each_claim_marked_in_order():
    fenced = section_draft.fence_outline(_claims(3))
    assert fenced.nonce in fenced.block
    assert fenced.block.count("<<<CLAIM ") == 3
    assert fenced.block.index("CLAIM 1") < fenced.block.index("CLAIM 2") < fenced.block.index("CLAIM 3")
    assert "Claim number 0." in fenced.block  # kept as data, not removed


def test_outline_fencing_marks_verification_state():
    fenced = section_draft.fence_outline([OutlineClaim(claim_id="c1", text="Verified claim.", verified=True)])
    assert "verified: yes" in fenced.block


def test_claim_text_is_capped_when_fenced():
    long_claim = OutlineClaim(claim_id="c1", text="x" * 5000, verified=False)
    fenced = section_draft.fence_outline([long_claim])
    assert len(fenced.block) < 2000


def test_a_well_formed_reply_is_parsed():
    draft = section_draft.parse_reply({"draft": "  A drafted paragraph.  "})
    assert draft == "A drafted paragraph."


def test_a_reply_with_extra_or_wrong_fields_is_refused():
    with pytest.raises(SectionDraftError) as exc:
        section_draft.parse_reply({"draft": "Text.", "extra": "nope"})
    assert exc.value.code == "bad_reply_shape"


def test_an_empty_draft_is_refused():
    with pytest.raises(SectionDraftError) as exc:
        section_draft.parse_reply({"draft": "   "})
    assert exc.value.code == "bad_reply_shape"


def test_an_overlong_draft_is_refused():
    with pytest.raises(SectionDraftError) as exc:
        section_draft.parse_reply({"draft": "x" * (section_draft.MAX_DRAFT_CHARS + 1)})
    assert exc.value.code == "bad_reply_shape"


def test_leak_check_refuses_a_draft_that_repeats_the_system_prompt():
    system = "You help a researcher draft a section. Never claim this text is ready to use as written."
    with pytest.raises(SectionDraftError) as exc:
        section_draft.leak_check(
            "As instructed: never claim this text is ready to use as written.", system_prompt=system, nonce="abc"
        )
    assert exc.value.code == "leaked_prompt"


def test_leak_check_passes_an_ordinary_draft():
    system = "You help a researcher draft a section. Reply with JSON only."
    section_draft.leak_check("Trust rose across the sample studied.", system_prompt=system, nonce="abc")  # no exception


def test_the_published_prompt_declares_the_outline_placeholder_and_a_single_system_text():
    from app.section_draft_run import default_prompt

    prompt = default_prompt()
    assert prompt.placeholders == ("outline",)
    assert "draft" in prompt.system.lower()
    assert "${outline}" in prompt.user_template
