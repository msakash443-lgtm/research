from app.number_consistency import find_mismatches


def test_a_number_that_matches_the_table_at_the_reported_precision_passes():
    result = find_mismatches("The mean was M = 3.42 (SD = 1.10).", {"M": 3.4166, "SD": 1.1})
    assert result == {"mismatches": [], "unverified": []}


def test_trailing_zeros_count_as_reported_precision():
    result = find_mismatches("SD = 1.10", {"SD": 1.1})
    assert result == {"mismatches": [], "unverified": []}


def test_a_number_that_differs_from_the_table_is_flagged_with_both_values():
    result = find_mismatches("The mean was M = 3.52.", {"M": 3.4166})
    assert result["mismatches"] == [{"label": "M", "text": "3.52", "table": 3.4166}]


def test_negative_values_are_compared_too():
    assert find_mismatches("r = -0.31", {"r": -0.3128})["mismatches"] == []
    assert find_mismatches("r = 0.31", {"r": -0.3128})["mismatches"] != []


def test_a_label_with_no_table_value_is_unverified_not_passed():
    result = find_mismatches("Scores were t = 2.10.", {"M": 3.4})
    assert result == {"mismatches": [], "unverified": ["t"]}


def test_a_half_way_value_is_rounded_half_up_as_apa_prints_it():
    assert find_mismatches("r = 0.13", {"r": 0.125})["mismatches"] == []
    assert find_mismatches("r = 0.12", {"r": 0.125})["mismatches"] != []


def test_a_negative_half_way_value_rounds_away_from_zero():
    assert find_mismatches("d = -0.13", {"d": -0.125})["mismatches"] == []


def test_a_tiny_negative_value_is_not_printed_with_a_minus_sign():
    assert find_mismatches("b = 0.00", {"b": -0.001})["mismatches"] == []
