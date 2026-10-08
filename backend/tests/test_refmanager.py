from app.refmanager import parse_bibtex, parse_ris, to_bibtex, to_ris

BIB = """
@comment{ignore me}
@article{smith2020,
  author = {Smith, Jane and Doe, John},
  title = {Deep {Learning} for {Evidence}
           Synthesis},
  journal = "Journal of Tests",
  year = 2020,
  doi = {10.1000/ABC.1},
}
@inproceedings{x, title={A talk}, booktitle={Conf}, year={2019}}
@misc{notitle, year={2000}}
"""

RIS = """TY  - JOUR
TI  - Deep learning for evidence synthesis
AU  - Smith, Jane
AU  - Doe, John
PY  - 2020/01/05
JO  - Journal of Tests
DO  - 10.1000/abc.1
ER  -

TY  - BOOK
AU  - Nobody
ER  -
"""


def test_bibtex_entries_become_records_and_untitled_ones_are_reported_not_invented():
    result = parse_bibtex(BIB)
    assert [r["title"] for r in result.records] == ["Deep Learning for Evidence Synthesis", "A talk"]
    first = result.records[0]
    assert first["authors"] == ["Smith, Jane", "Doe, John"]
    assert first["year"] == 2020 and first["venue"] == "Journal of Tests" and first["doi"] == "10.1000/ABC.1"
    assert result.records[1]["source_type"] == "conference"
    assert result.skipped == ["notitle: no title"]


def test_ris_entries_become_records_and_untitled_ones_are_reported_not_invented():
    result = parse_ris(RIS)
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec["authors"] == ["Smith, Jane", "Doe, John"] and rec["year"] == 2020 and rec["source_type"] == "article"
    assert len(result.skipped) == 1 and "no title" in result.skipped[0]


def test_bibtex_round_trip_keeps_the_fields():
    records = parse_bibtex(BIB).records
    again = parse_bibtex(to_bibtex(records)).records
    assert [r["title"] for r in again] == [r["title"] for r in records]
    assert again[0]["authors"] == records[0]["authors"] and again[0]["year"] == 2020


def test_ris_round_trip_keeps_the_fields():
    records = parse_ris(RIS).records
    again = parse_ris(to_ris(records)).records
    assert again == records


def test_duplicate_bibtex_keys_are_made_unique():
    recs = [{"title": "A", "authors": ["Lee, Ann"], "year": 2020}] * 2
    keys = [line for line in to_bibtex(recs).splitlines() if line.startswith("@")]
    assert len(set(keys)) == 2


def test_hostile_text_cannot_break_out_of_the_output_fields():
    rec = {"title": "x}\n@article{evil,", "authors": ["a\nER  - \nTY  - JOUR"], "source_type": "article"}
    assert len(parse_bibtex(to_bibtex([rec])).records) == 1
    assert len(parse_ris(to_ris([rec])).records) == 1


def test_garbage_input_gives_no_records_and_no_crash():
    assert parse_bibtex("@@@ {{{ not bibtex").records == []
    assert parse_ris("no tags here").records == []


def test_a_paren_delimited_bibtex_entry_keeps_every_field():
    text = '@article(smith2020, title = {Wages (and prices)}, author = {Smith, Jane}, year = 2020, doi = {10.1/x}, journal = "J (Econ)")'
    [record] = parse_bibtex(text).records
    assert record["title"] == "Wages (and prices)" and record["year"] == 2020
    assert record["doi"] == "10.1/x" and record["venue"] == "J (Econ)" and record["authors"] == ["Smith, Jane"]
