import ast
import pathlib

import pytest
from pydantic import ValidationError

from app.connectors import (
    Connector,
    ConnectorBase,
    ConnectorError,
    FullText,
    NotSupportedError,
    PaperRecord,
    SearchPage,
    SearchRequest,
)


def _record(**over):
    return PaperRecord(**{"connector": "fake", "external_id": "W1", "title": "A paper", **over})


def test_record_normalises_doi_and_is_immutable():
    record = _record(doi="https://doi.org/10.1000/ABC", authors=["A", "B"], year=2020)
    assert record.doi == "10.1000/abc"
    assert record.authors == ("A", "B")
    with pytest.raises(ValidationError):
        record.title = "changed"


@pytest.mark.parametrize(
    "bad",
    [
        {"doi": "not-a-doi"},
        {"oa_url": "javascript:alert(1)"},
        {"url": "file:///etc/passwd"},
        {"year": 5},
        {"title": ""},
        {"external_id": ""},
        {"unexpected": 1},
        {"fulltext_path": "/etc/passwd"},  # system-owned fields can't be smuggled in
    ],
)
def test_record_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        _record(**bad)


def test_blank_optional_values_become_none():
    record = _record(doi="  ", url="", oa_url=None)
    assert (record.doi, record.url, record.oa_url) == (None, None, None)


def test_search_request_bounds():
    assert SearchRequest(query="x").limit == 25
    for bad in ({"query": ""}, {"query": "x", "limit": 0}, {"query": "x", "limit": 201}):
        with pytest.raises(ValidationError):
            SearchRequest(**bad)


def test_fulltext_needs_text_and_http_url():
    assert FullText(source_url="https://e.test/a.pdf", text="body").license is None
    with pytest.raises(ValidationError):
        FullText(source_url="ftp://e.test/a", text="body")
    with pytest.raises(ValidationError):
        FullText(source_url="https://e.test/a", text="")


def test_unimplemented_capabilities_fail_loudly_not_empty():
    class SearchOnly(ConnectorBase):
        name = "searchonly"

        def search(self, request):
            return SearchPage(records=(_record(),), total=1)

    connector = SearchOnly()
    assert connector.search(SearchRequest(query="x")).records[0].title == "A paper"
    for call in (
        lambda: connector.get_by_id("W1"),
        lambda: connector.get_citations("W1"),
        lambda: connector.get_references("W1"),
        lambda: connector.get_fulltext("W1"),
    ):
        with pytest.raises(NotSupportedError) as info:
            call()
        assert "searchonly" in str(info.value)
        assert isinstance(info.value, ConnectorError)


def test_protocol_is_structural():
    assert isinstance(ConnectorBase(), Connector)

    class Incomplete:
        name = "x"

        def search(self, request): ...

    assert not isinstance(Incomplete(), Connector)


def test_connector_package_never_touches_the_database_or_imports_arc():
    """Connectors return records; callers store them. They also must not import ARC pipeline code."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors"
    forbidden = {"sqlalchemy", "researchclaw", "app.models", "app.database"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), (path.name, name)
