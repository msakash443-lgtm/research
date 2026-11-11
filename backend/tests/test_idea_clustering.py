from app.idea_clustering import Idea, cluster_ideas


def test_an_idea_repeated_with_different_case_and_punctuation_is_a_duplicate_of_the_first():
    result = cluster_ideas(
        [Idea("a", "Trust predicts knowledge sharing."), Idea("b", "trust PREDICTS knowledge sharing")]
    )
    assert result.duplicates == {"b": "a"}
    assert [c.member_ids for c in result.clusters] == [["a"]]


def test_ideas_sharing_most_of_their_content_words_are_clustered_and_unrelated_ones_are_not():
    result = cluster_ideas(
        [
            Idea("a", "trust remote teams knowledge sharing"),
            Idea("b", "trust remote teams sharing expertise"),
            Idea("c", "quantum lattice gravity"),
        ]
    )
    assert [c.member_ids for c in result.clusters] == [["a", "b"], ["c"]]


def test_a_cluster_names_the_terms_its_members_have_in_common():
    result = cluster_ideas(
        [Idea("a", "trust remote teams knowledge sharing"), Idea("b", "trust remote teams sharing expertise")]
    )
    assert result.clusters[0].shared_terms == ["remote", "sharing", "teams", "trust"]


def test_a_construct_is_a_term_in_at_least_two_distinct_ideas_and_lists_them():
    result = cluster_ideas(
        [Idea("a", "trust in remote teams"), Idea("b", "remote work and burnout"), Idea("c", "quantum lattice gravity")]
    )
    assert [(c.term, c.idea_ids) for c in result.constructs] == [("remote", ["a", "b"])]


def test_a_duplicate_does_not_count_as_a_second_idea_for_construct_extraction():
    result = cluster_ideas([Idea("a", "lattice gravity models"), Idea("b", "lattice gravity models")])
    assert result.constructs == []


def test_no_ideas_gives_an_empty_result():
    result = cluster_ideas([])
    assert (result.clusters, result.constructs, result.duplicates) == ([], [], {})
