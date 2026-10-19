import httpx
import pytest

from app import survey_google_forms as gf

FORM = {
    "items": [
        {"title": "Age", "questionItem": {"question": {"questionId": "q1", "textQuestion": {}}}},
        {"title": "Intro text"},  # no question: skipped
        {"title": "Agree", "questionGroupItem": {"questions": [
            {"questionId": "g1", "rowQuestion": {"title": "Trust"}},
            {"questionId": "g2", "rowQuestion": {"title": "Cost"}},
        ]}},
        {"title": "Age", "questionItem": {"question": {"questionId": "q2", "textQuestion": {}}}},
    ]
}


def _response(rid, created, answers):
    return {"responseId": rid, "createTime": created, "lastSubmittedTime": created, "answers": answers}


def test_table_has_meta_then_questions_in_form_order_with_unique_titles():
    responses = [_response("r1", "2026-10-01T10:00:00Z", {
        "q1": {"textAnswers": {"answers": [{"value": "34"}]}},
        "g1": {"textAnswers": {"answers": [{"value": "4"}]}},
        "q2": {"textAnswers": {"answers": [{"value": "x"}, {"value": "y"}]}},
    })]
    columns, rows = gf.table_from_form(FORM, responses)
    assert columns == ["response_id", "created_at", "submitted_at", "Age", "Agree / Trust", "Agree / Cost", "Age (2)"]
    assert rows == [["r1", "2026-10-01T10:00:00Z", "2026-10-01T10:00:00Z", "34", "4", "", "x; y"]]


def test_rows_are_ordered_by_creation_then_id_and_missing_answers_are_blank():
    responses = [_response("b", "2026-10-02T00:00:00Z", {}), _response("a", "2026-10-01T00:00:00Z", {})]
    _, rows = gf.table_from_form(FORM, responses)
    assert [r[0] for r in rows] == ["a", "b"]
    assert rows[0][3] == ""


def test_non_text_answers_are_marked_not_dropped():
    responses = [_response("r", "t", {"q1": {"fileUploadAnswers": {"answers": [{"fileId": "f"}]}}})]
    _, rows = gf.table_from_form(FORM, responses)
    assert rows[0][3] == "[non-text answer]"


@pytest.mark.parametrize("bad", ["", "a b", "../etc", "x" * 201, "id;drop"])
def test_form_id_must_be_a_plain_identifier(bad):
    with pytest.raises(gf.SurveyError):
        gf.check_form_id(bad)


def _transport(pages, form=FORM, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/v1/forms/F1":
            return httpx.Response(200, json=form)
        if request.url.path == "/v1/forms/F1/responses":
            token = request.url.params.get("pageToken") or "first"
            return httpx.Response(200, json=pages[token])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_fetch_follows_pagination_and_sends_bearer_token_only_to_forms_host():
    seen = []
    pages = {
        "first": {"responses": [_response("r1", "2026-10-01T00:00:00Z", {})], "nextPageToken": "p2"},
        "p2": {"responses": [_response("r2", "2026-10-02T00:00:00Z", {})]},
    }
    columns, rows = gf.fetch_table("F1", "secret-token", transport=_transport(pages, seen=seen))
    assert [r[0] for r in rows] == ["r1", "r2"]
    assert all(req.url.host == "forms.googleapis.com" for req in seen)
    assert all(req.headers["authorization"] == "Bearer secret-token" for req in seen)
def test_repeated_page_token_is_refused_instead_of_looping_forever():
    pages = {
        "first": {"responses": [_response("r1", "2026-10-01T00:00:00Z", {})], "nextPageToken": "repeat"},
        "repeat": {"responses": [], "nextPageToken": "repeat"},
    }
    with pytest.raises(gf.SurveyError, match="repeated a page token"):
        gf.fetch_table("F1", "secret-token", transport=_transport(pages))



@pytest.mark.parametrize("status,expected", [(401, "refused the access token"), (404, "could not find"), (500, "HTTP 500")])
def test_api_errors_are_named_and_never_echo_the_token(status, expected):
    def handler(request):
        return httpx.Response(status, json={"error": "nope"})
    with pytest.raises(gf.SurveyError) as info:
        gf.fetch_table("F1", "secret-token", transport=httpx.MockTransport(handler))
    assert expected in str(info.value)
    assert "secret-token" not in str(info.value)


def test_non_json_reply_is_refused():
    def handler(request):
        return httpx.Response(200, content=b"<html>")
    with pytest.raises(gf.SurveyError):
        gf.fetch_table("F1", "t", transport=httpx.MockTransport(handler))


def test_missing_token_and_bad_form_id_fail_before_any_request():
    with pytest.raises(gf.SurveyError, match="not set"):
        gf.fetch_table("F1", "  ", transport=httpx.MockTransport(lambda r: pytest.fail("network used")))
    with pytest.raises(gf.SurveyError):
        gf.fetch_table("../bad", "t", transport=httpx.MockTransport(lambda r: pytest.fail("network used")))


def test_response_cap_refuses_rather_than_truncates(monkeypatch):
    monkeypatch.setattr(gf, "MAX_RESPONSES", 1)
    pages = {"first": {"responses": [_response("a", "t", {}), _response("b", "t", {})]}}
    with pytest.raises(gf.SurveyError, match="refused, not truncated"):
        gf.fetch_table("F1", "t", transport=_transport(pages))
