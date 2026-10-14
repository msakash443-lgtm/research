import csv
import io

import pytest

from app.bibliometric_networks import build, citespace_csv, vosviewer_files


RECORDS = [
    {"source_id": "s1", "title": "First", "year": 2020,
     "reference_ids": ["r1", "r2", "r2"], "keywords": ["Climate", "Policy", "climate"]},
    {"source_id": "s2", "title": "Second", "year": None,
     "reference_ids": ["r1", "r2"], "keywords": ["climate", "Health"]},
    {"source_id": "s3", "title": "Third", "year": 2022,
     "reference_ids": ["r1", "r3"], "keywords": ["Policy", "Health"]},
]


def test_networks_count_distinct_per_source_references_and_keywords():
    result = build(RECORDS)
    assert result["co_citation"] == [
        {"left": "r1", "right": "r2", "weight": 2},
        {"left": "r1", "right": "r3", "weight": 1},
    ]
    assert result["bibliographic_coupling"] == [
        {"left": "s1", "right": "s2", "weight": 2},
        {"left": "s1", "right": "s3", "weight": 1},
        {"left": "s2", "right": "s3", "weight": 1},
    ]
    assert result["keyword_co_occurrence"] == [
        {"left": "climate", "right": "health", "weight": 1},
        {"left": "climate", "right": "policy", "weight": 1},
        {"left": "health", "right": "policy", "weight": 1},
    ]
    assert result["source_count"] == result["sources_with_references"] == result["sources_with_keywords"] == 3


def test_missing_metadata_is_not_synthesized_and_invalid_inputs_fail():
    assert build([{"source_id": "s1", "reference_ids": [], "keywords": []}])["co_citation"] == []
    with pytest.raises(ValueError, match="explicit lists"):
        build([{"source_id": "s1"}])
    with pytest.raises(ValueError, match="unique"):
        build([RECORDS[0], RECORDS[0]])


def test_vosviewer_and_citespace_exports_preserve_real_data_and_weights():
    map_text, network_text = vosviewer_files(build(RECORDS)["co_citation"])
    assert list(csv.reader(io.StringIO(map_text), delimiter="\t")) == [
        ["id", "label"], ["1", "r1"], ["2", "r2"], ["3", "r3"],
    ]
    assert list(csv.reader(io.StringIO(network_text), delimiter="\t")) == [
        ["1", "2", "2"], ["1", "3", "1"],
    ]
    rows = list(csv.DictReader(io.StringIO(citespace_csv(RECORDS))))
    assert rows[0]["cited_references"] == "r1; r2; r2"
    assert rows[1]["year"] == "" and rows[1]["authors"] == "" and rows[1]["doi"] == ""
