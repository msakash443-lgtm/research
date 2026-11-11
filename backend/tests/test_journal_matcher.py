from app.journal_matcher import JournalCandidate, rank


def test_matcher_is_explainable_and_preserves_source_timestamp():
    rows = rank([
        JournalCandidate("A Journal", ("health", "methods"), ("Scopus",), 1000, 30, "https://example/a", "2026-10-22"),
        JournalCandidate("B Journal", ("methods",), (), 500, 10, "https://example/b", "2026-10-22", True),
    ], scope=["health", "methods"], required_index="Scopus", max_apc=800)
    assert rows[0]["name"] == "A Journal"
    assert rows[0]["scope_overlap"] == 2
    assert rows[1]["flags"] == ["missing_index", "official_predatory_list"]
    assert rows[0]["source_url"] and rows[0]["checked_at"]
