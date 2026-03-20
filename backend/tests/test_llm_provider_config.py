from __future__ import annotations

from app.ai import openai_vision


def test_provider_helpers_prefer_supermarks_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_LLM_API_KEY", "sm-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("SUPERMARKS_LLM_BASE_URL", "https://api.doubleword.ai/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")

    assert openai_vision._provider_api_key() == "sm-key"
    assert openai_vision._provider_base_url() == "https://api.doubleword.ai/v1"
    assert openai_vision._provider_name() == "doubleword"


def test_provider_helpers_fall_back_to_openai_env(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SUPERMARKS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SUPERMARKS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.doubleword.ai/v1")

    assert openai_vision._provider_api_key() == "openai-key"
    assert openai_vision._provider_base_url() == "https://api.doubleword.ai/v1"
    assert openai_vision._provider_name() == "openai_compatible"


def test_front_page_provider_helpers_can_override_global_provider(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_LLM_API_KEY", "doubleword-key")
    monkeypatch.setenv("SUPERMARKS_LLM_BASE_URL", "https://api.doubleword.ai/v1")
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_API_KEY", "openai-front-page-key")
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert openai_vision._front_page_provider_name() == "openai_compatible"
    assert openai_vision._front_page_provider_api_key() == "openai-front-page-key"
    assert openai_vision._front_page_provider_base_url() is None


def test_front_page_openai_override_does_not_reuse_doubleword_key(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_LLM_API_KEY", "doubleword-key")
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "openai_compatible")
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert openai_vision._front_page_provider_api_key() == ""
