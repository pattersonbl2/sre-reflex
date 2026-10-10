import logging
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

log = logging.getLogger(__name__)

MAX_ALERTS_PER_WEBHOOK = 50
MAX_MAP_ENTRIES = 64
MAX_KEY_LEN = 128
MAX_VALUE_LEN = 4096
MAX_URL_LEN = 2048

Key = Annotated[str, StringConstraints(max_length=MAX_KEY_LEN)]
Value = Annotated[str, StringConstraints(max_length=MAX_VALUE_LEN)]


def _bounded_map(v: dict) -> dict:
    if len(v) > MAX_MAP_ENTRIES:
        raise ValueError(f"too many entries (max {MAX_MAP_ENTRIES})")
    return v


class AlertmanagerAlert(BaseModel):
    status: str = Field(max_length=32)
    labels: dict[Key, Value]
    annotations: dict[Key, Value] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = Field(default="", max_length=MAX_URL_LEN)
    fingerprint: str = Field(max_length=128)

    _bound_labels = field_validator("labels", "annotations")(_bounded_map)

    @property
    def alertname(self) -> str:
        return self.labels.get("alertname", "unknown")


class AlertmanagerWebhook(BaseModel):
    status: str = Field(max_length=32)
    alerts: list[AlertmanagerAlert]

    @field_validator("alerts")
    @classmethod
    def _cap_alerts(cls, v: list[AlertmanagerAlert]) -> list[AlertmanagerAlert]:
        # Truncate rather than reject so a large alert group is still partly triaged.
        if len(v) > MAX_ALERTS_PER_WEBHOOK:
            log.warning(
                "webhook carried %d alerts; processing only the first %d",
                len(v), MAX_ALERTS_PER_WEBHOOK,
            )
            return v[:MAX_ALERTS_PER_WEBHOOK]
        return v
