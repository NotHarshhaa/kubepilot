"""Provider-agnostic LLM factory (Gemini / OpenAI / Ollama)."""

from functools import lru_cache

from app.config.settings import get_settings


@lru_cache
def get_llm():
    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise RuntimeError(
                "Gemini provider selected but 'langchain-google-genai' is not installed. "
                "Install it with: pip install -e '.[gemini]'"
            ) from exc
        return ChatGoogleGenerativeAI(
            model=settings.model_name,
            temperature=settings.temperature,
            google_api_key=settings.google_api_key,
        )

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI provider selected but 'langchain-openai' is not installed. "
                "Install it with: pip install -e '.[openai]'"
            ) from exc
        return ChatOpenAI(
            model=settings.model_name,
            temperature=settings.temperature,
            api_key=settings.openai_api_key,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.model_name,
            base_url=settings.ollama_base_url,
            temperature=settings.temperature,
        )

    raise ValueError(f"Unknown LLM provider '{settings.llm_provider}' (expected gemini, openai, or ollama).")
