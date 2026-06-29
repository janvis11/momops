"""LLM provider routing for MomOps agents."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

import httpx

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal local envs
    anthropic = None  # type: ignore[assignment]

from momops.config import MomOpsSettings, get_settings

logger = logging.getLogger(__name__)

ChatRole = Literal["user", "assistant"]
ProviderName = Literal["groq", "openai", "claude"]


class ChatMessage(TypedDict):
    """Small shared chat message shape used by all supported providers."""

    role: ChatRole
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Text response with provider metadata for logs and tests."""

    provider: ProviderName
    model: str
    text: str


@dataclass(frozen=True)
class _Provider:
    name: ProviderName
    model: str
    api_key: str
    base_url: str | None = None


class LLMProviderError(RuntimeError):
    """Raised when configured LLM providers cannot produce a response."""


def configured_provider_names(settings: MomOpsSettings | None = None) -> list[ProviderName]:
    """Return configured provider names in runtime priority order."""
    return [provider.name for provider in _configured_providers(settings or get_settings())]


def has_configured_provider(settings: MomOpsSettings | None = None) -> bool:
    """Return whether any usable LLM provider is configured."""
    return bool(configured_provider_names(settings))


def complete_text(
    *,
    system: str,
    messages: Sequence[ChatMessage],
    max_tokens: int,
    settings: MomOpsSettings | None = None,
) -> LLMResponse:
    """Complete a chat request using Groq, then OpenAI, then Claude."""
    resolved_settings = settings or get_settings()
    providers = _configured_providers(resolved_settings)
    if not providers:
        raise LLMProviderError("No LLM provider API keys are configured")

    errors: list[str] = []
    for provider in providers:
        try:
            if provider.name == "claude":
                text = _complete_anthropic(provider, system, messages)
            else:
                text = _complete_openai_compatible(
                    provider,
                    system,
                    messages,
                    max_tokens,
                    timeout=resolved_settings.llm_timeout_seconds,
                )
            logger.debug("LLM response generated with %s:%s", provider.name, provider.model)
            return LLMResponse(provider=provider.name, model=provider.model, text=text)
        except Exception as exc:
            errors.append(f"{provider.name}: {exc}")
            logger.warning("LLM provider %s failed; trying next provider", provider.name)

    raise LLMProviderError("; ".join(errors))


async def complete_text_async(
    *,
    system: str,
    messages: Sequence[ChatMessage],
    max_tokens: int,
    settings: MomOpsSettings | None = None,
) -> LLMResponse:
    """Async variant of complete_text with the same provider priority."""
    resolved_settings = settings or get_settings()
    providers = _configured_providers(resolved_settings)
    if not providers:
        raise LLMProviderError("No LLM provider API keys are configured")

    errors: list[str] = []
    for provider in providers:
        try:
            if provider.name == "claude":
                text = await _complete_anthropic_async(provider, system, messages)
            else:
                text = await _complete_openai_compatible_async(
                    provider,
                    system,
                    messages,
                    max_tokens,
                    timeout=resolved_settings.llm_timeout_seconds,
                )
            logger.debug("Async LLM response generated with %s:%s", provider.name, provider.model)
            return LLMResponse(provider=provider.name, model=provider.model, text=text)
        except Exception as exc:
            errors.append(f"{provider.name}: {exc}")
            logger.warning("LLM provider %s failed; trying next provider", provider.name)

    raise LLMProviderError("; ".join(errors))


def _configured_providers(settings: MomOpsSettings) -> list[_Provider]:
    providers: list[_Provider] = []
    if settings.groq_api_key:
        providers.append(
            _Provider(
                name="groq",
                model=settings.groq_model,
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1/chat/completions",
            )
        )
    if settings.openai_api_key:
        providers.append(
            _Provider(
                name="openai",
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url="https://api.openai.com/v1/chat/completions",
            )
        )
    if settings.anthropic_api_key and anthropic is not None:
        providers.append(
            _Provider(
                name="claude",
                model=settings.anthropic_model,
                api_key=settings.anthropic_api_key,
            )
        )
    return providers


def _openai_compatible_payload(
    provider: _Provider,
    system: str,
    messages: Sequence[ChatMessage],
    max_tokens: int,
) -> dict[str, object]:
    chat_messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    chat_messages.extend(
        {"role": message["role"], "content": message["content"]} for message in messages
    )
    return {
        "model": provider.model,
        "messages": chat_messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def _complete_openai_compatible(
    provider: _Provider,
    system: str,
    messages: Sequence[ChatMessage],
    max_tokens: int,
    *,
    timeout: float,
) -> str:
    if provider.base_url is None:
        raise LLMProviderError(f"{provider.name} is missing a base URL")

    response = httpx.post(
        provider.base_url,
        headers={
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        },
        json=_openai_compatible_payload(provider, system, messages, max_tokens),
        timeout=timeout,
    )
    response.raise_for_status()
    return _extract_openai_compatible_text(response.json())


async def _complete_openai_compatible_async(
    provider: _Provider,
    system: str,
    messages: Sequence[ChatMessage],
    max_tokens: int,
    *,
    timeout: float,
) -> str:
    if provider.base_url is None:
        raise LLMProviderError(f"{provider.name} is missing a base URL")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            provider.base_url,
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            },
            json=_openai_compatible_payload(provider, system, messages, max_tokens),
        )
    response.raise_for_status()
    return _extract_openai_compatible_text(response.json())


def _complete_anthropic(
    provider: _Provider,
    system: str,
    messages: Sequence[ChatMessage],
) -> str:
    if anthropic is None:
        raise LLMProviderError("anthropic package is not installed")

    client = anthropic.Anthropic(api_key=provider.api_key)
    response = client.messages.create(
        model=provider.model,
        max_tokens=1024,
        system=system,
        messages=list(messages),
    )
    return _extract_anthropic_text(response)


async def _complete_anthropic_async(
    provider: _Provider,
    system: str,
    messages: Sequence[ChatMessage],
) -> str:
    if anthropic is None:
        raise LLMProviderError("anthropic package is not installed")

    client = anthropic.AsyncAnthropic(api_key=provider.api_key)
    response = await client.messages.create(
        model=provider.model,
        max_tokens=1024,
        system=system,
        messages=list(messages),
    )
    return _extract_anthropic_text(response)


def _extract_openai_compatible_text(data: object) -> str:
    if not isinstance(data, dict):
        raise LLMProviderError("LLM response was not an object")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderError("LLM response contained no choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMProviderError("LLM choice was not an object")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMProviderError("LLM choice contained no message")

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content

    raise LLMProviderError("LLM response contained no text")


def _extract_anthropic_text(response: object) -> str:
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        raise LLMProviderError("Claude response contained no content")

    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            return text

    raise LLMProviderError("Claude response contained no text")

