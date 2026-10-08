import pytest

from app.agreement import cohen_kappa, conflicts, meets_threshold


def pairs(a, b):
    return list(zip(a, b))


def test_perfect_agreement_across_labels_is_one():
    r = cohen_kappa(pairs("IIEEMM", "IIEEMM"))
    assert r.kappa == 1.0 and r.percent_agreement == 1.0 and r.band == "almost perfect"


def test_a_textbook_example_matches_the_published_value():
    # 50 items: both yes 20, A yes/B no 5, A no/B yes 10, both no 15 -> kappa = 0.4 (Wikipedia worked example)
    data = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    r = cohen_kappa(data)
    assert r.n == 50 and r.agreed == 35 and r.percent_agreement == 0.7
    assert r.kappa == pytest.approx(0.4) and r.band == "fair"


def test_chance_level_agreement_is_zero():
    data = [("y", "y")] * 25 + [("y", "n")] * 25 + [("n", "y")] * 25 + [("n", "n")] * 25
    assert cohen_kappa(data).kappa == pytest.approx(0.0)


def test_systematic_disagreement_is_negative_and_named_so():
    r = cohen_kappa([("y", "n"), ("n", "y")] * 10)
    assert r.kappa == pytest.approx(-1.0) and r.band == "worse than chance"


def test_no_items_gives_no_kappa_and_says_why():
    r = cohen_kappa([])
    assert r.kappa is None and r.percent_agreement is None and "No item" in r.note


def test_one_label_everywhere_is_undefined_not_perfect():
    r = cohen_kappa([("y", "y")] * 40)
    assert r.kappa is None and r.percent_agreement == 1.0 and "undefined" in r.note


def test_the_kappa_paradox_is_flagged():
    data = [("n", "n")] * 94 + [("y", "n")] * 3 + [("n", "y")] * 3
    r = cohen_kappa(data)
    assert r.percent_agreement == pytest.approx(0.94) and r.kappa < 0.6 and "nearly all" in r.note


def test_small_samples_carry_a_warning():
    assert "rough guide" in cohen_kappa(pairs("IEIE", "IEIE")).note


def test_the_matrix_counts_every_pair():
    r = cohen_kappa([("I", "E"), ("I", "I"), ("E", "E"), ("I", "E")])
    assert r.matrix == {"E": {"E": 1, "I": 0}, "I": {"E": 2, "I": 1}}


def test_conflicts_lists_only_disagreements_in_order():
    assert conflicts({"s1": ("I", "I"), "s2": ("I", "E"), "s3": ("E", "M")}) == ["s2", "s3"]


def test_an_undefined_kappa_never_meets_a_threshold():
    assert meets_threshold(0.81, 0.8) and not meets_threshold(0.79, 0.8) and not meets_threshold(None, 0.0)
