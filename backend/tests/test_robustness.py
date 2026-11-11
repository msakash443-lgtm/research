import pytest

from app import robustness


def test_holm_matches_hand_worked_example():
    # sorted: 0.005*4=0.02, 0.01*3=0.03, 0.03*2=0.06, 0.04*1=0.04 -> running max
    adjusted = robustness.holm_adjust([0.01, 0.04, 0.03, 0.005])
    assert adjusted == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_is_monotone_and_capped_at_one():
    # 0.5*3=1.5 -> 1.0 caps; running max keeps 0.9*1=0.9 from dropping below 1.0
    adjusted = robustness.holm_adjust([0.5, 0.6, 0.9])
    assert adjusted == pytest.approx([1.0, 1.0, 1.0])
    assert all(a >= p for a, p in zip(adjusted, [0.5, 0.6, 0.9]))


def test_holm_empty_and_bad_values():
    assert robustness.holm_adjust([]) == []
    with pytest.raises(ValueError):
        robustness.holm_adjust([0.2, 1.5])
    with pytest.raises(ValueError):
        robustness.holm_adjust([float("nan")])


def test_fingerprint_ignores_key_order_and_last_bit_noise():
    a = {"b": [1, 2.0], "a": 0.1 + 0.2}
    b = {"a": 0.3, "b": [1, 2.0]}
    assert robustness.fingerprint_results(a) == robustness.fingerprint_results(b)


def test_fingerprint_changes_when_a_visible_number_changes():
    base = {"mean": 3.14159}
    assert robustness.fingerprint_results(base) != robustness.fingerprint_results({"mean": 3.14160})


def test_fingerprint_refuses_non_finite_and_unknown_types():
    with pytest.raises(ValueError):
        robustness.fingerprint_results({"x": float("inf")})
    with pytest.raises(TypeError):
        robustness.fingerprint_results({"x": object()})


def test_holm_refuses_boolean_p_values():
    # codex nonblocking nit (M5.4.1): bool is an int subclass, so True was accepted as p = 1
    with pytest.raises(ValueError):
        robustness.holm_adjust([True, 0.2])
    with pytest.raises(ValueError):
        robustness.holm_adjust([False])


def test_fingerprint_refuses_non_string_keys_instead_of_merging_them():
    # codex nonblocking nit (M5.4.1): {1: "a"} and {"1": "b"} used to collide silently after str() coercion
    with pytest.raises(TypeError):
        robustness.fingerprint_results({1: "a"})
    assert robustness.fingerprint_results({"1": "b"}) != robustness.fingerprint_results({"1": "a"})


def test_signed_zero_and_integral_floats_hash_like_their_integer_values():
    # codex nonblocking nit (M5.4.1): 0.0 vs -0.0 and 1 vs 1.0 hashed differently
    fp = robustness.fingerprint_results
    assert fp({"x": 0.0}) == fp({"x": -0.0}) == fp({"x": 0})
    assert fp({"x": 1.0}) == fp({"x": 1})
