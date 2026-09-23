from datetime import datetime, timedelta
from statistics import median

import httpx

from sre_reflex.alerts import AlertmanagerAlert

STEP_S = 60


def episodes(timestamps: list[float], step: float) -> list[tuple[float, float]]:
    if not timestamps:
        return []
    ts = sorted(timestamps)
    runs = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev > step * 1.5:
            runs.append((start, prev))
            start = t
        prev = t
    runs.append((start, prev))
    return runs


def summarize_history(eps: list[tuple[float, float]], now: datetime) -> str:
    if not eps:
        return "No previous firings of this alert in the last 7 days."
    cutoff = now.timestamp() - 2 * STEP_S
    closed = [(s, e) for s, e in eps if e < cutoff]
    if not closed:
        return f"Fired {len(eps)} times in 7 days; none resolved yet."
    durations = [(e - s) / 60 + 1 for s, e in closed]
    return (
        f"Fired {len(eps)} times in 7 days; resolved {len(closed)} times, "
        f"median duration {median(durations):.0f}m."
    )


class HistoryCollector:
    name = "history"

    def __init__(self, client: httpx.AsyncClient, prometheus_url: str):
        self.client = client
        self.url = prometheus_url.rstrip("/")

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        query = f'ALERTS{{alertname="{alert.alertname}",alertstate="firing"}}'
        r = await self.client.get(
            f"{self.url}/api/v1/query_range",
            params={
                "query": query,
                "start": (now - timedelta(days=7)).timestamp(),
                "end": now.timestamp(),
                "step": str(STEP_S),
            },
        )
        r.raise_for_status()
        eps = []
        for series in r.json()["data"]["result"]:
            eps += episodes([float(ts) for ts, _ in series["values"]], STEP_S)
        return [summarize_history(eps, now)]
