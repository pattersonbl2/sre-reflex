from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    model_server_url: str = "http://model-server:8000"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    database_url: str = "postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex"
    ntfy_url: str = ""
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


def check_startup_security(settings: "Settings") -> None:
    """Refuse to run with an insecure/incomplete production configuration.

    This is intentionally NOT run for CLI paths that don't need these values
    (e.g. `sre-reflex migrate` / `sre-reflex infer-labels`), and is skipped
    whenever a test/stub pipeline is injected directly into `create_app`.
    """
    key = settings.label_hmac_key
    if not key or key == "change-me" or len(key.encode()) < 32:
        raise ValueError(
            "LABEL_HMAC_KEY is missing, the default 'change-me', or shorter than "
            "32 bytes. Set it to a random secret of at least 32 characters, e.g. "
            "`openssl rand -hex 32`."
        )
    if not settings.ntfy_url:
        raise ValueError(
            "NTFY_URL is not set. Refusing to start with no ntfy server configured "
            "(the public https://ntfy.sh default has been removed); set NTFY_URL "
            "to your own ntfy instance."
        )
