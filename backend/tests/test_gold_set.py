import json

import jsonschema
import pytest
from tests.gold.loader import csv_to_gold_set, load_gold_set, HERE


def test_gold_set_is_valid():
    load_gold_set()


def test_gold_set_populated_before_measuring():
    data = load_gold_set()
    if not data["papers"]:
        pytest.skip("gold set not supplied yet (X.15 / Q10)")
    assert data["topic"] and data["supplied_by"]
    assert sum(p.get("known_relevant", False) for p in data["papers"]) >= 1


def test_csv_roundtrip(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("id,doi,title,abstract,label,known_relevant,reason\n"
                 "a1,,T1,,Include,yes,on topic\n", encoding="utf-8")
    d = csv_to_gold_set(f, "t", "s")
    assert d["papers"][0]["label"] == "include" and d["papers"][0]["known_relevant"]

    jsonschema.validate(d, json.loads((HERE / "gold_set.schema.json").read_text()))


def _papers_csv(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("id,doi,title,abstract,label,known_relevant,reason\n"
                 "a1,,T1,,include,yes,on topic\n", encoding="utf-8")
    return f


def test_extractions_attach_to_their_paper_with_the_source_quote(tmp_path):
    e = tmp_path / "e.csv"
    e.write_text("id,field,value,source_quote\n"
                 "a1,sample_size,120,\"We recruited 120 participants.\"\n", encoding="utf-8")
    d = csv_to_gold_set(_papers_csv(tmp_path), "t", "s", extractions_csv=e)
    assert d["papers"][0]["extraction"] == {
        "sample_size": {"value": "120", "source_quote": "We recruited 120 participants."}}
    jsonschema.validate(d, json.loads((HERE / "gold_set.schema.json").read_text()))


def test_extraction_for_an_unknown_paper_is_refused(tmp_path):
    e = tmp_path / "e.csv"
    e.write_text("id,field,value,source_quote\nzz,n,1,q\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown paper id"):
        csv_to_gold_set(_papers_csv(tmp_path), "t", "s", extractions_csv=e)


def test_an_extraction_value_without_a_quote_fails_validation(tmp_path):
    e = tmp_path / "e.csv"
    e.write_text("id,field,value,source_quote\na1,n,1,\n", encoding="utf-8")
    d = csv_to_gold_set(_papers_csv(tmp_path), "t", "s", extractions_csv=e)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(d, json.loads((HERE / "gold_set.schema.json").read_text()))

def _dataset_rows():
    rows = [{"openalex_id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1/{i}",
             "label_included": "1" if i < 2 else "0"} for i in range(10)]
    meta = {f"W{i}": {"title": f"T{i}", "abstract": None} for i in range(10) if i != 5}
    return rows, meta


def test_abstract_is_rebuilt_from_the_inverted_index_in_word_order():
    from tests.gold.loader import abstract_from_inverted_index
    assert abstract_from_inverted_index({"b": [1], "a": [0], "c": [2, 3]}) == "a b c c"
    assert abstract_from_inverted_index(None) is None


def test_dataset_subset_keeps_every_include_and_a_seeded_exclude_sample():
    from tests.gold.loader import dataset_rows_to_gold_set
    rows, meta = _dataset_rows()
    args = dict(n_exclude=4, seed=7, topic="t", supplied_by="s", source_note="n")
    d = dataset_rows_to_gold_set(rows, meta, **args)
    labels = [p["label"] for p in d["papers"]]
    assert labels.count("include") == 2 and labels.count("exclude") == 4
    assert all(p["known_relevant"] for p in d["papers"] if p["label"] == "include")
    assert "W5" not in [p["id"] for p in d["papers"]]  # no title -> skipped, not invented
    assert d == dataset_rows_to_gold_set(rows, meta, **args)  # deterministic
    jsonschema.validate(d, json.loads((HERE / "gold_set.schema.json").read_text()))


def test_an_included_record_without_a_title_is_refused_not_dropped():
    from tests.gold.loader import dataset_rows_to_gold_set
    rows, meta = _dataset_rows()
    del meta["W0"]
    with pytest.raises(ValueError, match="without a title"):
        dataset_rows_to_gold_set(rows, meta, n_exclude=1, seed=1, topic="t", supplied_by="s",
                                 source_note="n")


def test_a_record_with_no_openalex_id_is_skipped_when_excluded_and_refused_when_included():
    from tests.gold.loader import dataset_rows_to_gold_set
    rows, meta = _dataset_rows()
    rows[7]["openalex_id"] = ""
    kw = dict(n_exclude=4, seed=1, topic="t", supplied_by="s", source_note="n")
    assert len(dataset_rows_to_gold_set(rows, meta, **kw)["papers"]) == 6
    rows[0]["openalex_id"] = ""
    with pytest.raises(ValueError, match="without a title"):
        dataset_rows_to_gold_set(rows, meta, **kw)


def test_two_dataset_rows_for_the_same_work_become_one_paper_and_an_include_wins():
    from tests.gold.loader import dataset_rows_to_gold_set
    rows, meta = _dataset_rows()
    rows.append({"openalex_id": "https://openalex.org/W0", "doi": "", "label_included": "0"})
    rows.insert(0, {"openalex_id": "https://openalex.org/W9", "doi": "", "label_included": "0"})
    rows.append({"openalex_id": "https://openalex.org/W9", "doi": "", "label_included": "1"})
    d = dataset_rows_to_gold_set(rows, meta, n_exclude=5, seed=1, topic="t", supplied_by="s",
                                 source_note="n")
    ids = [p["id"] for p in d["papers"]]
    assert len(ids) == len(set(ids))
    assert next(p for p in d["papers"] if p["id"] == "W9")["label"] == "include"
    jsonschema.validate(d, json.loads((HERE / "gold_set.schema.json").read_text()))
