import pytest

from app.idea_scoping import Reference
from app.semantic_novelty import VectorError, cosine, semantic_novelty


def _ref(i: int, title: str, year: int | None = 2020) -> Reference:
    return Reference(f"s{i}", title, year, "")


def test_cosine_of_parallel_and_orthogonal_vectors():
    assert cosine([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_distinctiveness_is_one_minus_the_closest_similarity():
    result = semantic_novelty([1.0, 0.0], [(_ref(1, "Near"), [1.0, 0.1]), (_ref(2, "Far"), [0.0, 1.0])])
    assert result.distinctiveness == pytest.approx(1 - cosine([1.0, 0.0], [1.0, 0.1]), abs=1e-3)
    assert [c.source_id for c in result.comparators] == ["s1", "s2"]  # closest first, all shown


def test_comparators_are_capped_and_sorted_by_similarity():
    refs = [(_ref(i, f"T{i}"), [1.0, float(i) / 10]) for i in range(1, 9)]
    result = semantic_novelty([1.0, 0.0], refs)
    assert len(result.comparators) == 5
    sims = [c.similarity for c in result.comparators]
    assert sims == sorted(sims, reverse=True)


def test_nothing_to_compare_with_gives_no_distinctiveness_score():
    result = semantic_novelty([1.0, 0.0], [])
    assert result.distinctiveness is None and result.comparators == []


@pytest.mark.parametrize("idea, ref", [
    ([0.0, 0.0], [1.0, 0.0]),  # all-zero idea vector
    ([1.0, 0.0], [1.0, 0.0, 0.0]),  # lengths differ
])
def test_unusable_vectors_are_refused(idea, ref):
    with pytest.raises(VectorError):
        semantic_novelty(idea, [(_ref(1, "X"), ref)])
