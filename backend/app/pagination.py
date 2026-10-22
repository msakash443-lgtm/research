"""Opt-in keyset pagination for list endpoints (X.142).

A cursor is the opaque, URL-safe encoding of the id of the last row on the previous page. The
next page is every row that sorts after that row, compared column to column inside the database
(`created_at < (SELECT created_at FROM ... WHERE id = :cursor)` and so on), never against a
timestamp sent back by the client: SQLite stores `server_default=now()` values with a different
text format from bound parameters, so a timestamp cursor would skip or repeat rows there.

Every order column sorts descending and the last one must be the primary key, so the order is
total and no two rows tie. Without a `limit` a route behaves exactly as before.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, Response
from sqlalchemy import and_, exists, false, or_, select
from sqlalchemy.orm import InstrumentedAttribute

NEXT_CURSOR_HEADER = "X-Next-Cursor"
MAX_PAGE_SIZE = 500


class InvalidCursor(ValueError):
    pass


def encode_cursor(row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> uuid.UUID:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        return uuid.UUID(bytes=raw)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursor("Invalid cursor") from exc


def after_row(model: Any, order: Sequence[InstrumentedAttribute], row_id: uuid.UUID):
    """WHERE clause: rows that sort after row `row_id` under `order` (all descending, id last)."""
    if order[-1] is not model.id:
        raise ValueError("the last order column must be the primary key")
    anchor = [select(column).where(model.id == row_id).scalar_subquery() for column in order]
    clauses = []
    for i, column in enumerate(order):
        equal_before = [order[j] == anchor[j] for j in range(i)]
        clauses.append(and_(*equal_before, column < anchor[i]))
    return or_(*clauses) if clauses else false()


def paginate(db, query, model: Any, order: Sequence[InstrumentedAttribute], limit: int | None,
             cursor: str | None, response: Response) -> list:
    """Run `query` (already filtered to what the caller may see) in `order`, one page at a time.

    With no `limit` every row comes back and `cursor` must be absent. A cursor must name a row
    that `query` itself can return, so a cursor from another project or list is refused (400).
    """
    query = query.order_by(*(column.desc() for column in order))
    if limit is None:
        if cursor is not None:
            raise HTTPException(status_code=400, detail="cursor needs limit")
        return list(db.scalars(query))
    if cursor is not None:
        try:
            row_id = decode_cursor(cursor)
        except InvalidCursor:
            raise HTTPException(status_code=400, detail="Invalid cursor") from None
        if not db.scalar(select(exists(query.where(model.id == row_id).order_by(None).subquery()))):
            raise HTTPException(status_code=400, detail="Invalid cursor")
        query = query.where(after_row(model, order, row_id))
    rows = list(db.scalars(query.limit(limit + 1)))
    if len(rows) > limit:
        rows = rows[:limit]
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(rows[-1].id)
    return rows
