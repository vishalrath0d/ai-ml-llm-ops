"""Provider registry / factory -- maps a provider name to an adapter class.
Kept as a plain dict + function (not a metaclass registry) so it's obvious
at a glance which providers exist."""
from __future__ import annotations

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseLLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider

_PROVIDERS = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def get_provider(name: str) -> BaseLLMProvider:
    key = (name or "").strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider '{name}'. Valid options: {sorted(_PROVIDERS)}"
        )
    return _PROVIDERS[key]()


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)
