from app import research_plan
from tests.api_helpers import create_project, create_source, sign_in


def _empty():
    return research_plan.build(idea_brief=None, landscape_scan=None, gaps=[], questions=[], objectives=[], hypotheses=[], analysis_plans=[], milestones=[])


def test_an_objective_without_a_question_is_listed_apart_and_never_attached_to_one():
    question = {"id": "q1", "text": "Why?", "type": "relational", "gap_ids": []}
    linked = {"id": "o1", "research_question_id": "q1", "statement": "Measure it", "measure": None, "deliverable": None}
    loose = {"id": "o2", "research_question_id": None, "statement": "Stay in budget", "measure": None, "deliverable": None}
    plan = research_plan.build(
        idea_brief=None, landscape_scan=None, gaps=[], questions=[question], objectives=[linked, loose], hypotheses=[], analysis_plans=[], milestones=[]
    )
    section = plan["research_questions"]
    assert [o["id"] for o in section["items"][0]["objectives"]] == ["o1"]
    assert [o["id"] for o in section["project_objectives"]] == ["o2"]


def test_markdown_of_an_empty_plan_says_not_written_for_every_section():
    text = research_plan.to_markdown(_empty(), "Empty")
    assert text.count(research_plan.NOT_WRITTEN) == 6  # design and validity carry their own explanation instead
    assert research_plan.DESIGN_NOTE in text and research_plan.VALIDITY_NOTE in text


def test_an_empty_project_returns_every_section_marked_not_written():
    client = sign_in("plan-empty@example.com")
    project_id = create_project(client, "Plan")
    plan = client.get(f"/api/projects/{project_id}/research-plan").json()
    assert set(research_plan.SECTIONS) == set(plan)
    assert {name: section["written"] for name, section in plan.items() if name != "milestones"} == {name: False for name in research_plan.SECTIONS if name != "milestones"}
    assert plan["milestones"]["written"] is True  # the stage chain always exists
    assert sum(m["current"] for m in plan["milestones"]["items"]) == 1
    assert all(m["gate_status"] == "pending" for m in plan["milestones"]["items"] if m["gate"])
    assert client.get(f"/api/projects/{project_id}/research-plan", params={"format": "md"}).text.count(research_plan.NOT_WRITTEN) == 5


def test_a_gap_question_and_objective_appear_linked_by_id_and_unselected_gaps_and_retired_objectives_do_not():
    client = sign_in("plan-linked@example.com")
    project_id = create_project(client, "Plan")
    a, b = create_source(client, project_id, "A"), create_source(client, project_id, "B")
    base = f"/api/projects/{project_id}"
    gap_id = client.post(f"{base}/gaps", json={"type": "empirical", "statement": "Selected gap", "supporting_paper_ids": [a, b]}).json()["id"]
    client.post(f"{base}/gaps", json={"type": "empirical", "statement": "Unselected gap", "supporting_paper_ids": [a, b]})
    client.patch(f"{base}/gaps/{gap_id}", json={"selected": True})
    question_id = client.post(f"{base}/research-questions", json={"text": "Does X affect Y?", "type": "relational", "gap_ids": [gap_id]}).json()["id"]
    objective_id = client.post(f"{base}/objectives", json={"statement": "Estimate the effect", "research_question_id": question_id}).json()["id"]
    retired_id = client.post(f"{base}/objectives", json={"statement": "Dropped aim"}).json()["id"]
    client.post(f"{base}/objectives/{retired_id}/retire", json={})

    plan = client.get(f"{base}/research-plan").json()
    assert [g["id"] for g in plan["gaps"]["items"]] == [gap_id]
    question = plan["research_questions"]["items"][0]
    assert question["id"] == question_id and question["gap_ids"] == [gap_id]
    assert [o["id"] for o in question["objectives"]] == [objective_id]
    assert plan["research_questions"]["project_objectives"] == []
    md = client.get(f"{base}/research-plan", params={"format": "md"}).text
    assert gap_id in md and question_id in md and objective_id in md and "Dropped aim" not in md
