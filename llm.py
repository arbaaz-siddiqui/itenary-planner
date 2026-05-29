"""llm — LLM provider factory.

Switch providers via `LLM_PROVIDER` env var. No code changes needed.

Currently supported:
    - openrouter   (default for dev)
    - anthropic    (Claude direct API)
    - selfhosted   (vLLM with OpenAI-compatible endpoint; Qwen 2.5 72B target for prod)
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from core import InvalidSettingError, MissingApiKey
from settings import get_llm_settings


def build_anthropic_llm(*, temperature: float = 0.3, max_tokens: int = 4096) -> ChatAnthropic:
    s = get_llm_settings()
    if not s.anthropic_api_key:
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic",
            provider="anthropic",
        )
    return ChatAnthropic(
        model_name=s.anthropic_model,
        anthropic_api_key=s.anthropic_api_key,
        temperature=temperature,
        max_tokens_to_sample=max_tokens,
        timeout=120,
        stop=None,
    )


def build_openrouter_llm(*, temperature: float = 0.3, max_tokens: int = 4096) -> ChatOpenAI:
    s = get_llm_settings()
    if not s.openrouter_api_key:
        raise MissingApiKey(
            "OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter",
            provider="openrouter",
        )
    return ChatOpenAI(
        model=s.openrouter_model,
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120,
    )


def build_selfhosted_llm(*, temperature: float = 0.3, max_tokens: int = 4096) -> ChatOpenAI:
    """Self-hosted Qwen via vLLM (OpenAI-compatible endpoint).

    Spin up vLLM like:
        vllm serve Qwen/Qwen2.5-72B-Instruct \\
            --tensor-parallel-size 4 \\
            --max-model-len 32768 \\
            --enable-auto-tool-choice \\
            --tool-call-parser hermes
    """
    s = get_llm_settings()
    return ChatOpenAI(
        model=s.selfhosted_model,
        api_key=s.selfhosted_api_key or "EMPTY",
        base_url=s.selfhosted_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=180,
    )


def build_llm(*, temperature: float = 0.3, max_tokens: int = 4096) -> BaseChatModel:
    """Factory — reads LLM_PROVIDER, returns the right LLM."""
    s = get_llm_settings()
    if s.provider == "anthropic":
        return build_anthropic_llm(temperature=temperature, max_tokens=max_tokens)
    if s.provider == "openrouter":
        return build_openrouter_llm(temperature=temperature, max_tokens=max_tokens)
    if s.provider == "selfhosted":
        return build_selfhosted_llm(temperature=temperature, max_tokens=max_tokens)
    raise InvalidSettingError(
        f"Unknown LLM_PROVIDER: {s.provider!r}",
        provider=s.provider,
        supported=["anthropic", "openrouter", "selfhosted"],
    )


def describe_current_provider() -> str:
    s = get_llm_settings()
    if s.provider == "anthropic":
        return f"Anthropic — {s.anthropic_model}"
    if s.provider == "openrouter":
        return f"OpenRouter — {s.openrouter_model}"
    if s.provider == "selfhosted":
        return f"Self-hosted — {s.selfhosted_model} ({s.selfhosted_base_url})"
    return f"Unknown provider: {s.provider!r}"


def get_active_model_id() -> str:
    """Return the model identifier for the currently active provider."""
    s = get_llm_settings()
    if s.provider == "anthropic":
        return s.anthropic_model
    if s.provider == "openrouter":
        return s.openrouter_model
    if s.provider == "selfhosted":
        return s.selfhosted_model
    return "unknown"
