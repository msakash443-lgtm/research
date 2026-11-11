from app.active_learning import rank_by_relevance, score, tokenize, train


def test_tokenize_lowercases_and_drops_short_tokens():
    assert tokenize("Remote Work & Wellbeing (2021)!") == ["remote", "work", "wellbeing", "2021"]


def test_no_examples_of_one_class_means_nothing_can_be_learned():
    assert train([], ["opinion piece, not empirical"]) is None
    assert train(["a randomised controlled trial"], []) is None
    assert train([], []) is None


def test_a_record_closer_to_the_positive_vocabulary_scores_higher():
    clf = train(
        positive_texts=["randomised controlled trial of remote work outcomes", "empirical study of remote work wellbeing"],
        negative_texts=["opinion piece on remote work", "editorial commentary, not empirical"],
    )
    assert clf is not None
    like_positive = score(clf, "a randomised controlled trial of wellbeing outcomes")
    like_negative = score(clf, "an opinion editorial commentary")
    assert like_positive > like_negative


def test_an_unseen_word_is_ignored_not_penalised():
    clf = train(["alpha beta"], ["gamma delta"])
    baseline = score(clf, "alpha")
    with_unknown_word = score(clf, "alpha zzzzneverseen")
    assert with_unknown_word == baseline  # the unseen word contributed 0 to both sides


def test_rank_by_relevance_sorts_highest_first_and_keeps_order_on_ties():
    clf = train(["remote work wellbeing study"], ["opinion piece editorial"])
    items = ["c", "a", "b"]
    texts = {"a": "remote work wellbeing randomised trial", "b": "opinion editorial commentary", "c": "remote work wellbeing randomised trial"}
    ranked = rank_by_relevance(clf, items, lambda x: texts[x])
    assert ranked[-1] == "b"  # clearly the least relevant, always last
    assert set(ranked[:2]) == {"a", "c"} and ranked.index("a") < ranked.index("b")
    # "a" and "c" score identically (same text): ties keep their original relative order
    assert ranked.index("c") < ranked.index("a")
