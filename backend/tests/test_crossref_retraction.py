from app.connectors.crossref import to_record

NOTICE_RELATION = [{"DOI": "10.1000/notice", "type": "retraction", "updated": "2024-01-01"}]


def test_a_paper_listing_a_retraction_notice_under_updated_by_is_flagged_retracted():
    record = to_record({"DOI": "10.1000/p1", "title": ["A retracted paper"], "updated-by": NOTICE_RELATION})

    assert record is not None
    assert "retracted" in record.quality_flags


def test_a_retraction_notice_itself_updating_another_work_is_not_flagged_retracted():
    record = to_record({"DOI": "10.1000/notice", "title": ["Retraction: A retracted paper"], "update-to": NOTICE_RELATION})

    assert record is not None
    assert "retracted" not in record.quality_flags


def test_an_updated_by_relation_of_another_type_does_not_flag_the_paper():
    record = to_record({
        "DOI": "10.1000/p2",
        "title": ["A corrected paper"],
        "updated-by": [{"DOI": "10.1000/erratum", "type": "correction"}],
    })

    assert record is not None
    assert "retracted" not in record.quality_flags
