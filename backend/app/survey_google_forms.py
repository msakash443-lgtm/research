"""Google Forms connector (M5.2, spec 5.8.1).

Reads one form's questions and responses with the owner's OAuth access token and turns them into a plain table
(columns, rows) that the existing dataset ingest stores as a raw, hashed version. Nothing is written here.

The token comes from settings (`GOOGLE_FORMS_ACCESS_TOKEN`), never from the database, and is never echoed in an error.
Only the Google Forms API host is contacted. Responses are stored by Google; that is covered by the owner's consent
decision recorded in plan.md (2026-10-17).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

BASE_URL = "https://forms.googleapis.com/v1"
FORM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
MAX_RESPONSES = 10_000
PAGE_SIZE = 500
META_COLUMNS = ["response_id", "created_at", "submitted_at"]
TIMEOUT_SECONDS = 30.0


class SurveyError(ValueError):
    """A problem the caller can show: bad form id, unusable reply, or the API refused. Never carries the token."""


def check_form_id(form_id: str) -> str:
    form_id = (form_id or "").strip()
    if not FORM_ID_RE.match(form_id):
        raise SurveyError("The Google Form id may only contain letters, digits, '-' and '_'.")
    return form_id


def _get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = client.get(f"{BASE_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        raise SurveyError(f"Google Forms could not be reached ({type(exc).__name__}).") from exc
    if response.status_code == 401 or response.status_code == 403:
        raise SurveyError(
            "Google Forms refused the access token. Check GOOGLE_FORMS_ACCESS_TOKEN has not expired and was granted "
            "forms.body.readonly and forms.responses.readonly."
        )
    if response.status_code == 404:
        raise SurveyError("Google Forms could not find that form for this token.")
    if response.status_code != 200:
        raise SurveyError(f"Google Forms answered HTTP {response.status_code}.")
    try:
        body = response.json()
    except ValueError as exc:
        raise SurveyError("Google Forms sent a reply that is not JSON.") from exc
    if not isinstance(body, dict):
        raise SurveyError("Google Forms sent an unexpected reply shape.")
    return body


def fetch_form(client: httpx.Client, form_id: str) -> dict[str, Any]:
    return _get(client, f"/forms/{form_id}")


def fetch_responses(client: httpx.Client, form_id: str) -> list[dict[str, Any]]:
    """Every response, following `nextPageToken`, up to MAX_RESPONSES. Refuses to truncate silently."""
    responses: list[dict[str, Any]] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"pageSize": PAGE_SIZE}
        if token:
            if token in seen_tokens:
                raise SurveyError("The survey service repeated a page token; import is refused.")
            seen_tokens.add(token)
            params["pageToken"] = token
        page = _get(client, f"/forms/{form_id}/responses", params)
        responses.extend(page.get("responses") or [])
        if len(responses) > MAX_RESPONSES:
            raise SurveyError(f"This form has more than {MAX_RESPONSES} responses; import is refused, not truncated.")
        token = page.get("nextPageToken")
        if not token:
            return responses


def _question_columns(form: dict[str, Any]) -> list[tuple[str, str]]:
    """(question id, column title) in form order. Grid questions get one column per row."""
    out: list[tuple[str, str]] = []
    for item in form.get("items") or []:
        title = (item.get("title") or "").strip() or "untitled"
        question_item = item.get("questionItem")
        group_item = item.get("questionGroupItem")
        if question_item and question_item.get("question", {}).get("questionId"):
            out.append((question_item["question"]["questionId"], title))
        elif group_item:
            for question in group_item.get("questions") or []:
                if not question.get("questionId"):
                    continue
                row_title = ((question.get("rowQuestion") or {}).get("title") or "").strip()
                out.append((question["questionId"], f"{title} / {row_title}" if row_title else title))
    return out


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def _answer_text(answer: dict[str, Any] | None) -> str:
    if not answer:
        return ""
    text_answers = (answer.get("textAnswers") or {}).get("answers") or []
    if text_answers:
        return "; ".join(str(a.get("value", "")) for a in text_answers)
    return "[non-text answer]"


def table_from_form(form: dict[str, Any], responses: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    """Flatten a form and its responses into (columns, rows). Rows are ordered by creation time, then response id."""
    questions = _question_columns(form)
    question_titles = _unique([title for _, title in questions])
    columns = META_COLUMNS + question_titles
    ordered = sorted(responses, key=lambda r: (str(r.get("createTime", "")), str(r.get("responseId", ""))))
    rows: list[list[str]] = []
    for response in ordered:
        answers = response.get("answers") or {}
        row = [
            str(response.get("responseId", "")),
            str(response.get("createTime", "")),
            str(response.get("lastSubmittedTime", "")),
        ]
        row.extend(_answer_text(answers.get(qid)) for qid, _ in questions)
        rows.append(row)
    return columns, rows


def fetch_table(form_id: str, access_token: str, *, transport: httpx.BaseTransport | None = None) -> tuple[list[str], list[list[str]]]:
    """Fetch one form's responses as a table. `transport` lets tests run without the network."""
    form_id = check_form_id(form_id)
    if not access_token or not access_token.strip():
        raise SurveyError("GOOGLE_FORMS_ACCESS_TOKEN is not set on this server.")
    with httpx.Client(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=transport,
    ) as client:
        form = fetch_form(client, form_id)
        responses = fetch_responses(client, form_id)
    return table_from_form(form, responses)
