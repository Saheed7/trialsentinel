"""Single entry point for chat models, so the provider is a configuration choice."""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from trialsentinel.core.config import Settings, get_settings


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    s = settings or get_settings()
    common = {
        "model": s.llm_model,
        "temperature": s.llm_temperature,
        "timeout": s.llm_timeout_s,
        "max_retries": 2,
    }
    if s.llm_provider == "ollama":
        # Ollama serves an OpenAI-compatible API; the key is required by the client but unused.
        return ChatOpenAI(base_url=s.ollama_base_url, api_key="ollama", **common)
    if s.llm_provider == "openai":
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else ""
        if not key:
            raise RuntimeError("TS_LLM_PROVIDER=openai requires TS_OPENAI_API_KEY")
        return ChatOpenAI(api_key=key, **common)
    raise ValueError(f"unsupported LLM provider: {s.llm_provider}")
