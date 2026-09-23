from datetime import datetime

from pydantic import BaseModel


class AlertmanagerAlert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str

    @property
    def alertname(self) -> str:
        return self.labels.get("alertname", "unknown")


class AlertmanagerWebhook(BaseModel):
    status: str
    alerts: list[AlertmanagerAlert]
