"""Application settings, loaded from environment variables and an optional .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM provider: gemini | openai | ollama | anthropic
    llm_provider: str = "ollama"
    model_name: str = "llama3.1"
    temperature: float = 0.2

    google_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Kubernetes
    kubeconfig: str = "~/.kube/config"
    kube_context: str | None = None
    default_namespace: str = "default"

    # Observability
    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"

    # GitHub
    github_token: str | None = None

    # Safety
    read_only: bool = False
    require_approval: bool = True
    audit_log_path: str = "logs/audit.jsonl"

    # Notifications & Webhooks
    webhook_url: str | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
