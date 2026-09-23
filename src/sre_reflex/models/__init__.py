import httpx

from sre_reflex.config import Settings
from sre_reflex.models.base import DecisionModel
from sre_reflex.models.fake import FakeModel
from sre_reflex.models.ollama import OllamaModel
from sre_reflex.models.openjev import OpenJevModel


def build_model(name: str, client: httpx.AsyncClient, settings: Settings) -> DecisionModel:
    if name == "fake":
        return FakeModel()
    if name == "openjev":
        return OpenJevModel(client, settings.model_server_url, settings.openjev_timeout_s)
    if name == "ollama":
        return OllamaModel(
            client, settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s
        )
    raise ValueError(f"unknown model: {name}")
