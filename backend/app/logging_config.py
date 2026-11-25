"""One log format for the web and worker processes (X.145).

Without this, the web process had no handler for the `app.*` loggers, so their INFO lines were
dropped and their errors reached stderr without a time or logger name. Log records hold ids,
counts and statuses only, never secrets, prompts, model answers or source text (same rule as
the audit payloads).
"""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Send `app.*` records to stderr at INFO. Calling it again, or after a host (uvicorn, pytest)
    has configured the root logger, changes nothing. httpx/httpcore are held at WARNING because
    their INFO lines carry full request URLs, and connector API keys travel in query strings."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
