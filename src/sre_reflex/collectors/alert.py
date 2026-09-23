from datetime import datetime

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.scrub import scrub

_SKIP_LABELS = {"alertname", "severity", "prometheus"}


class AlertCollector:
    name = "alert"

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        severity = alert.labels.get("severity", "none")
        where = ", ".join(
            f"{k}={v}" for k, v in sorted(alert.labels.items()) if k not in _SKIP_LABELS
        )
        minutes = max(0, int((now - alert.startsAt).total_seconds() // 60))
        lines = [
            (
                f"Alert {alert.alertname} (severity={severity}) on {where or 'no labels'}, "
                f"firing {minutes}m."
            )
        ]
        for key in ("summary", "description"):
            if value := alert.annotations.get(key):
                lines.append(f"{key.capitalize()}: {scrub(value)}")
        return lines
