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