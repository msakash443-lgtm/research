import pytest

from app.front_matter import FrontMatterError, build, canonical_text, source_hash


def test_build_binds_output_to_canonical_document_version_hash():
    result = build("abstract", "  A concise abstract.  ", source_version_id="v1", source_content_json={"x": 1})
    assert result.text == "A concise abstract."
    assert result.source_version_id == "v1"
    assert result.source_hash == source_hash({"x": 1})


def test_keywords_are_normalized_and_unique():
    assert canonical_text("keywords", "AI; Evidence, synthesis") == "AI, Evidence, synthesis"
    with pytest.raises(FrontMatterError):
        canonical_text("keywords", "AI, ai")


@pytest.mark.parametrize("kind", ["abstract", "highlights", "cover_letter"])
def test_text_outputs_require_nonempty_bounded_text(kind):
    with pytest.raises(FrontMatterError):
        canonical_text(kind, "  ")
    with pytest.raises(FrontMatterError):
        canonical_text(kind, "x" * 20_001)


def test_unknown_kind_is_refused():
    with pytest.raises(FrontMatterError):
        canonical_text("summary", "text")
