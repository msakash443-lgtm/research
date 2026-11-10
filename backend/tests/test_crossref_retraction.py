from app.connectors.crossref import to_record


def test_crossref_retraction_update_relation_sets_the_source_flag():
    record = to_record({
        "DOI": "10.1000/p1",
        "title": ["A retracted paper"],
        "update-to": [{"DOI": "10.1000/notice", "type": "retraction", "updated": "2024-01-01"}],
    })

    assert record is not None
    assert "retracted" in record.quality_flags
