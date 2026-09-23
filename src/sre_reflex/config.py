from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    model_server_url: str = "http://model-server:8000"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    database_url: str = "postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex"
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = "sre-reflex"
    ntfy_token: str = ""
    public_label_url: str = "http://localhost:8080"
    label_hmac_key: str = "change-me"
    metric_queries_file: str = ""
    enabled_models: str = "openjev,ollama"
    collector_timeout_s: float = 3.0
    openjev_timeout_s: float = 5.0
    ollama_timeout_s: float = 30.0
    state_token_cap: int = 800

    @property
    def model_names(self) -> list[str]:
        return [m.strip() for m in self.enabled_models.split(",") if m.strip()]
