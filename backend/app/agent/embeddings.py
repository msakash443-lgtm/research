"""Client for an OpenAI-compatible `/embeddings` endpoint (M3.8.1).

The reply is checked strictly: one finite vector per input, all the same length. Anything else fails
the step (plan rule 22); there is no zero vector or other stand-in for a missing embedding.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.config import Settings


class OpenAICompatibleEmbeddings:
    def __init__(self, settings: Settings, *, meter: Any = None):
        self.model = settings.embedding_model
        self._base_url = settings.embedding_api_base_url or settings.llm_api_base_url
        self._api_key = settings.llm_api_key
        self._timeout_seconds = settings.llm_timeout_seconds
        self._meter = meter  # same interface as the chat adapter's: before_call() / after_call(model, response)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.model or not self._base_url:
            raise LLMConfigurationError(
                "Thematic clustering needs an embedding model. Set EMBEDDING_MODEL (and EMBEDDING_API_BASE_URL "
                "if it isn't served from LLM_API_BASE_URL)."
            )
        if not texts:
            return []
        base_url = self._base_url.rstrip("/")
        url = base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        if self._meter is not None:
            self._meter.before_call()
        try:
            with httpx.Client(timeout=self._timeout_seconds, follow_redirects=False) as client:
                response = client.post(url, headers=headers, json={"model": self.model, "input": texts})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise LLMResponseError("The configured embedding service could not be reached.") from exc
        except ValueError as exc:
            raise LLMResponseError("The configured embedding service returned invalid JSON.") from exc
        if self._meter is not None:
            self._meter.after_call(self.model, data)
        return parse_embeddings(data, len(texts))


def parse_embeddings(data: Any, expected: int) -> list[list[float]]:
    """The vectors in input order, or LLMResponseError saying what was wrong with the reply."""
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != expected:
        raise LLMResponseError(f"The embedding service returned {len(items) if isinstance(items, list) else 'no'} vectors for {expected} texts.")
    vectors: list[list[float] | None] = [None] * expected
    for position, item in enumerate(items):
        index = item.get("index", position) if isinstance(item, dict) else None
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < expected or vectors[index] is not None:
            raise LLMResponseError("The embedding service returned vectors with missing or repeated indexes.")
        if (
            not isinstance(vector, list) or not vector
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector)
        ):
            raise LLMResponseError("The embedding service returned a vector that isn't a list of finite numbers.")
        vectors[index] = [float(x) for x in vector]
    if len({len(v) for v in vectors}) != 1:  # type: ignore[arg-type]
        raise LLMResponseError("The embedding service returned vectors of different lengths.")
    if any(not any(v) for v in vectors):  # type: ignore[union-attr]
        raise LLMResponseError("The embedding service returned an all-zero vector.")
    return vectors  # type: ignore[return-value]
