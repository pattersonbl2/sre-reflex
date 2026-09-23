import httpx

from sre_reflex.config import Settings
from sre_reflex.models.base import DecisionModel
from sre_reflex.models.fake import FakeModel


def build_model(name: str, client: httpx.AsyncClient, settings: Settings) -> DecisionModel:
    if name == "fake":
        return FakeModel()
    raise ValueError(f"unknown model: {name}")
