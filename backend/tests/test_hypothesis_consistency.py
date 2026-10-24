from app.hypothesis_consistency import find_hypothesis_alignment


def test_all_declared_hypotheses_present_in_results_and_conclusions_are_consistent():
    report = find_hypothesis_alignment(
        {
            "hypotheses": "H1: treatment improves sleep. H2: effect differs by age.",
            "results": "For H1, sleep improved. H2 was examined in the subgroup analysis.",
            "conclusions": "H1 is supported; H2 remains uncertain.",
        }
    )

    assert report["consistent"] is True
    assert report["labels"] == {"hypotheses": ["H1", "H2"], "results": ["H1", "H2"], "conclusions": ["H1", "H2"]}
    assert report["missing"] == {"results": [], "conclusions": []}
    assert report["extra"] == {"results": [], "conclusions": []}


def test_missing_downstream_hypothesis_is_reported_by_section():
    report = find_hypothesis_alignment(
        {
            "hypotheses": "H1: treatment helps. H2: treatment is safe.",
            "results": "H1 improved the outcome.",
            "conclusions": "H1 is supported.",
        }
    )

    assert report["consistent"] is False
    assert report["missing"] == {"results": ["H2"], "conclusions": ["H2"]}


def test_undeclared_downstream_label_is_reported_as_extra():
    report = find_hypothesis_alignment(
        {
            "hypotheses": "H1: treatment helps.",
            "results": "H1 improved the outcome; H3 was exploratory.",
            "conclusions": "H1 is supported.",
        }
    )

    assert report["consistent"] is False
    assert report["extra"] == {"results": ["H3"], "conclusions": []}


def test_labels_are_case_insensitive_and_allow_whitespace():
    report = find_hypothesis_alignment(
        {
            "hypotheses": "h 1 predicts an improvement.",
            "results": "H1 was tested.",
            "conclusions": "h 1 is supported.",
        }
    )

    assert report["consistent"] is True
    assert report["labels"]["hypotheses"] == ["H1"]
