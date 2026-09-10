from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


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


class OpenAICompatibleLLM:
    """Small adapter for a configured OpenAI-compatible chat-completions endpoint.

    Keeping the HTTP request here lets the product remain provider-neutral. The
    endpoint, model, and API key are deployment secrets, not user input.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        api_key = self.settings.llm_api_key
        if not self.settings.llm_api_base_url or not self.settings.llm_model:
            raise LLMConfigurationError(
                "The agent is not connected to an LLM. Start Ollama and configure LLM_API_BASE_URL and LLM_MODEL."
            )

        base_url = self.settings.llm_api_base_url.rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1800,
        }
        try:
            timeout = httpx.Timeout(self.settings.llm_timeout_seconds, connect=10)
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise LLMResponseError("Ollama is not reachable. Start it with 'ollama serve' and install the configured model.") from exc
        except httpx.TimeoutException as exc:
            raise LLMResponseError("The local LLM request timed out. Try a smaller model or shorter input.") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMResponseError(f"The LLM service rejected the request: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise LLMResponseError("The configured LLM service could not be reached.") from exc
        except ValueError as exc:
            raise LLMResponseError("The configured LLM service returned invalid JSON.") from exc

        try:
            answer = _message_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("The configured LLM service returned an unexpected response.") from exc
        if not answer:
            raise LLMResponseError("The configured LLM service returned an empty answer.")
        return answer
