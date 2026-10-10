from __future__ import annotations

import json
from typing import Any

import httpx
import jsonschema
from pydantic import SecretStr

from app.config import Settings


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


class LLMSchemaError(LLMResponseError):
    """The model never produced output that satisfies the required schema."""


def _message_text(content: Any) -> str:
    """Normalize the common OpenAI-compatible message response shapes."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts).strip()
    return ""


def _parse_json(text: str) -> Any:
    """Parse the model's reply as JSON, tolerating one surrounding ```json fence."""
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            body = "\n".join(lines[1:-1])
    return json.loads(body)


def _violation(output: str, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Return (parsed value, None) if valid, else (None, short reason)."""
    try:
        value = _parse_json(output)
    except ValueError:
        return None, "the reply was not valid JSON"
    error = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(schema).iter_errors(value))
    if error is not None:
        where = "/".join(str(part) for part in error.absolute_path) or "(top level)"
        return None, f"at {where}: {error.message[:200]}"
    return value, None


_UNSET = object()


class OpenAICompatibleLLM:
    """Small adapter for a configured OpenAI-compatible chat-completions endpoint.

    Keeping the HTTP request here lets the product remain provider-neutral. The
    endpoint, model, and API key are deployment secrets, not user input.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        api_key: SecretStr | None = _UNSET,  # type: ignore[assignment]
        api_base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        require_api_key: bool = True,
        meter: Any = None,
    ):
        self.settings = settings
        self._api_key = settings.llm_api_key if api_key is _UNSET else api_key
        self._api_base_url = api_base_url if api_base_url is not None else settings.llm_api_base_url
        self._model = model if model is not None else settings.llm_model
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds
        self._require_api_key = require_api_key
        self._meter = meter  # optional: .before_call() may refuse, .after_call(model, response) records usage

    @classmethod
    def for_participant_data(cls, settings: Settings, *, meter: Any = None) -> "OpenAICompatibleLLM":
        """An adapter for prompts that may contain participant/sensitive data.

        Use this instead of the plain constructor for consent text, raw responses,
        transcripts, or anything else a participant provided. It points at the
        local/self-hosted endpoint (`LOCAL_LLM_API_BASE_URL`/`LOCAL_LLM_MODEL`) and
        never falls back to the third-party `LLM_API_BASE_URL` silently: if no local
        model is configured and `PARTICIPANT_DATA_REQUIRES_LOCAL_MODEL` is true (the
        default), it fails loudly instead (plan rule 22 — no silent substitution).
        Most local servers (Ollama, LM Studio, vLLM without `--api-key`) need no key,
        so one is not required here even though it is for the default constructor.
        Pass the project's `meter` (M0.9.1) so these calls count toward its token budget too.
        """
        if settings.local_llm_api_base_url and settings.local_llm_model:
            return cls(
                settings,
                api_key=settings.local_llm_api_key,
                api_base_url=settings.local_llm_api_base_url,
                model=settings.local_llm_model,
                timeout_seconds=settings.local_llm_timeout_seconds,
                require_api_key=False,
                meter=meter,
            )
        if settings.participant_data_requires_local_model:
            raise LLMConfigurationError(
                "Participant data requires a locally configured model. Set "
                "LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL, or set "
                "PARTICIPANT_DATA_REQUIRES_LOCAL_MODEL=false to explicitly allow the "
                "third-party model configured in LLM_API_BASE_URL to see participant data."
            )
        return cls(settings, meter=meter)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.validate_configuration()

        base_url = self._api_base_url.rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1800,
        }
        if self._meter is not None:
            self._meter.before_call()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        try:
            with httpx.Client(timeout=self._timeout_seconds, follow_redirects=False) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise LLMResponseError("The configured LLM service could not be reached.") from exc
        except ValueError as exc:
            raise LLMResponseError("The configured LLM service returned invalid JSON.") from exc

        if self._meter is not None:
            self._meter.after_call(self._model, data)
        try:
            answer = _message_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("The configured LLM service returned an unexpected response.") from exc
        if not answer:
            raise LLMResponseError("The configured LLM service returned an empty answer.")
        return answer

    def validate_configuration(self) -> None:
        """Raise when this adapter does not have the configuration needed to call its model."""
        if (self._require_api_key and not self._api_key) or not self._api_base_url or not self._model:
            raise LLMConfigurationError(
                "The agent is not connected to an LLM yet. Configure LLM_API_KEY, LLM_API_BASE_URL, and LLM_MODEL."
            )

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        max_attempts: int | None = None,
    ) -> Any:
        """Call the model until its reply is JSON that satisfies `schema`.

        A violating reply is rejected and the model is asked again, told what was
        wrong. After `max_attempts` the step fails with LLMSchemaError: there is
        no default value and no repair of the output (plan rule 22). The invalid
        reply itself is not included in the error, only the violation.
        """
        jsonschema.Draft202012Validator.check_schema(schema)
        attempts = max_attempts if max_attempts is not None else self.settings.llm_schema_max_attempts
        if attempts < 1:
            raise LLMConfigurationError("llm_schema_max_attempts must be at least 1.")
        prompt = user_prompt
        reason = ""
        for _ in range(attempts):
            output = self.complete(system_prompt, prompt)
            value, reason = _violation(output, schema)
            if reason is None:
                return value
            prompt = (
                f"{user_prompt}\n\nYour previous reply was rejected: {reason}. "
                "Reply again with only JSON that matches the required schema."
            )
        raise LLMSchemaError(
            f"The model's reply did not match the required format after {attempts} attempts ({reason})."
        )
