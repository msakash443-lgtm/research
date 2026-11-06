import uuid

from fastapi.testclient import TestClient

from app import codebook
from app.main import app

COLUMNS = ["id", "score", "group", "note"]
ROWS = [
    ["1", "4", "a", ""],
    ["2", "5", "b", "x"],
    ["3", "", "a", ""],
    ["4", "2", "c", "y"],
]


def _by_name(variables):
    return {v["name"]: v for v in variables}


def test_numeric_columns_get_missing_count_range_and_mean():
    v = _by_name(codebook.build(COLUMNS, ROWS))["score"]
    assert v["type"] == "numeric"
    assert v["n"] == 3 and v["missing"] == 1
    assert v["min"] == 2 and v["max"] == 5
    assert round(v["mean"], 4) == round((4 + 5 + 2) / 3, 4)
    assert "levels" not in v


def test_small_text_columns_list_levels_by_count_then_value_and_skip_missing():
    v = _by_name(codebook.build(COLUMNS, ROWS))["group"]
    assert v["type"] == "categorical"
    assert v["levels"] == [{"value": "a", "count": 2}, {"value": "b", "count": 1}, {"value": "c", "count": 1}]
    note = _by_name(codebook.build(COLUMNS, ROWS))["note"]
    assert note["missing"] == 2 and note["n"] == 2


def test_free_text_columns_report_a_count_not_their_values():
    rows = [[str(i), f"free text answer number {i}"] for i in range(30)]
    v = _by_name(codebook.build(["id", "answer"], rows))["answer"]
    assert v["type"] == "text"
    assert v["distinct_count"] == 30
    assert "levels" not in v


def test_an_all_blank_column_is_empty_and_nan_is_not_a_number():
    variables = _by_name(codebook.build(["a", "b"], [["", "nan"], ["", "1"]]))
    assert variables["a"]["type"] == "empty" and variables["a"]["missing"] == 2
    assert variables["b"]["type"] != "numeric"


def test_short_free_text_with_no_repeated_values_is_not_copied_as_levels():
    v = _by_name(codebook.build(["name"], [["Anna Smith"], ["Bo Li"]]))["name"]
    assert v["type"] == "text"
    assert v["distinct_count"] == 2
    assert "levels" not in v


def test_repeated_names_with_spaces_are_never_listed_as_levels():
    v = _by_name(codebook.build(["name"], [["Anna Smith"], ["Bo Li"], ["Anna Smith"]]))["name"]
    assert v["type"] == "text"
    assert v["distinct_count"] == 2
    assert "levels" not in v


def test_long_repeated_values_are_never_listed_as_levels():
    v = _by_name(codebook.build(["answer"], [["a long answer"], ["a long answer"]]))["answer"]
    assert v["type"] == "text" and "levels" not in v


def test_text_that_only_looks_numeric_to_python_is_not_numeric():
    variables = _by_name(codebook.build(["n"], [["1_000"], ["2_000"]]))
    assert variables["n"]["type"] != "numeric"


def test_extreme_values_do_not_overflow_the_mean():
    v = _by_name(codebook.build(["x"], [["1e308"], ["1e308"]]))["x"]
    assert v["type"] == "numeric"
    assert v["mean"] == 1e308


def test_every_variable_has_a_definition_left_for_the_researcher():
    for v in codebook.build(COLUMNS, ROWS):
        assert v["definition"] is None


def test_codebook_route_is_read_only_and_follows_column_order():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"cb-{uuid.uuid4().hex}@example.com", "display_name": "C"})
    pid = client.post("/api/projects", json={"title": "Codebook"}).json()["id"]
    csv_text = "id,score,group\n1,4,a\n2,5,b\n"
    did = client.post(f"/api/projects/{pid}/datasets", json={"name": "raw", "csv_text": csv_text}).json()["id"]
    r = client.get(f"/api/projects/{pid}/datasets/{did}/codebook")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["name"] for v in body["variables"]] == ["id", "score", "group"]
    assert body["source_sha256"] == client.get(f"/api/projects/{pid}/datasets/{did}").json()["sha256"]
    assert client.get(f"/api/projects/{pid}/datasets").json()[0]["version_no"] == 1
