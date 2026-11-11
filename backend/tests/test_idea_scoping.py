from app.idea_scoping import MAX_COMPARATORS, Reference, feasibility_gaps, novelty


def _ref(source_id: str, title: str, text: str = "", year: int | None = 2020) -> Reference:
    return Reference(source_id=source_id, title=title, year=year, text=text)


def test_the_closest_comparator_is_the_source_sharing_the_most_idea_words():
    refs = [
        _ref("a", "Trust in remote teams", "remote work and trust"),
        _ref("b", "Coffee prices", "bean supply"),
    ]
    result = novelty("trust in remote teams during lockdown", refs)
    assert [c.source_id for c in result.comparators] == ["a"]
    assert result.comparators[0].shared_terms == ["remote", "teams", "trust"]


def test_distinctiveness_is_one_minus_the_closest_overlap_and_the_overlap_is_shown():
    refs = [_ref("a", "Trust in remote teams", "remote work")]
    result = novelty("trust remote teams lockdown", refs)
    # 3 of the idea's 4 content words are shared with the closest comparator.
    assert result.comparators[0].overlap == 0.75
    assert result.distinctiveness == 0.25


def test_with_no_project_sources_there_is_no_score_and_no_comparator():
    result = novelty("trust in remote teams", [])
    assert result.distinctiveness is None
    assert result.comparators == []


def test_with_sources_but_no_shared_words_the_idea_is_fully_distinct_and_says_so_by_listing_nothing():
    result = novelty("trust in remote teams", [_ref("a", "Soil erosion", "rivers and sediment")])
    assert result.comparators == []
    assert result.distinctiveness == 1.0


def test_an_idea_with_only_stop_words_is_not_scored():
    result = novelty("what is it", [_ref("a", "What is trust")])
    assert result.idea_terms == []
    assert result.distinctiveness is None
    assert result.comparators == []


def test_only_the_top_comparators_are_listed_and_ties_are_ordered_by_title():
    refs = [_ref(f"s{i}", f"Trust study {i:02d}", "trust") for i in range(MAX_COMPARATORS + 3)]
    result = novelty("trust", refs)
    assert len(result.comparators) == MAX_COMPARATORS
    assert [c.title for c in result.comparators] == sorted(c.title for c in result.comparators)


def test_feasibility_gaps_list_unanswered_and_unsure_items_in_checklist_order():
    answers = {"method_fit": "unsure", "data_availability": "ok", "ethics_risk": "concern"}
    assert feasibility_gaps(answers) == ["method_fit", "time_and_skills"]


def test_a_blocker_or_concern_is_an_answer_not_a_gap():
    answers = {"data_availability": "blocker", "method_fit": "concern", "ethics_risk": "ok", "time_and_skills": "ok"}
    assert feasibility_gaps(answers) == []
