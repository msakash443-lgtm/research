"""Non-AI triage suggestions for a note (X.31.2): pure token overlap, no network, no model.

A project is suggested when at least 15% of the note's distinct content words appear in it; a source when at
least 20% do, up to three sources. The suggested project's stage is offered alongside it.
"""

from app.note_suggest import ProjectSummary, SourceSummary, suggest

WAGE = ProjectSummary(
    id="p1", title="Gender wage inequality", description="Remote work and pay gaps", stage="scoped",
    question_texts=("Does remote work widen the wage gap?",),
)
LFPR = ProjectSummary(id="p2", title="Labour force participation", description="Women's LFPR trends", stage="idea", question_texts=())
SOURCES = [
    SourceSummary(id="s1", project_id="p1", title="Sen 2019 on care burden and the gender wage gap"),
    SourceSummary(id="s2", project_id="p2", title="Participation of women in Kerala"),
]


def _of_kind(suggestions, kind):
    return [item for item in suggestions if item.kind == kind]


def test_suggests_the_matching_project_with_its_stage():
    result = suggest("Does remote work widen the gender wage gap in Kerala?", [WAGE, LFPR], SOURCES)

    projects = _of_kind(result, "project")
    assert [item.target_id for item in projects] == ["p1"]
    assert projects[0].label == "Gender wage inequality"
    stages = _of_kind(result, "stage")
    assert [(item.target_id, item.label) for item in stages] == [("p1", "scoped")]


def test_suggests_sources_at_or_above_the_threshold_with_their_project():
    result = suggest("Does remote work widen the gender wage gap in Kerala?", [WAGE, LFPR], SOURCES)

    sources = _of_kind(result, "source")
    # s1 shares gender, wage, gap; s2 shares only "kerala" (1 of 7 = 0.14, below 0.2).
    assert [(item.target_id, item.project_id) for item in sources] == [("s1", "p1")]


def test_nothing_is_suggested_for_a_note_with_no_shared_words():
    assert suggest("Bananas and apples", [WAGE, LFPR], SOURCES) == []


def test_a_note_of_only_stop_words_suggests_nothing():
    assert suggest("The of and to", [WAGE, LFPR], SOURCES) == []


def test_a_single_shared_word_below_fifteen_percent_is_not_enough():
    # 11 distinct content words, one of which is "remote": 1/11 = 0.09.
    note = "alpha beta gamma delta epsilon zeta eta theta iota kappa remote"
    project = ProjectSummary(id="p3", title="remote", description="", stage="idea", question_texts=())
    assert _of_kind(suggest(note, [project], []), "project") == []


def test_at_most_three_sources_are_suggested():
    sources = [SourceSummary(id=f"s{i}", project_id="p1", title="gender wage gap study") for i in range(5)]
    assert len(_of_kind(suggest("gender wage gap", [WAGE], sources), "source")) == 3


def test_output_is_deterministic():
    note = "Does remote work widen the gender wage gap in Kerala?"
    assert suggest(note, [WAGE, LFPR], SOURCES) == suggest(note, [WAGE, LFPR], SOURCES)
