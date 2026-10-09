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