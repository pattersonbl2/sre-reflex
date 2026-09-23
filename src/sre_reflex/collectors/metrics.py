import math
from datetime import datetime, timedelta
from string import Template
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from pydantic import BaseModel

from sre_reflex.alerts import AlertmanagerAlert

WINDOW = timedelta(minutes=30)
MAX_QUERIES = 3


class MetricQuery(BaseModel):
    name: str
    query: str
    unit: str = ""


def load_metric_queries(path: str) -> dict[str, list[MetricQuery]]:
    if not path:
        return {}
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return {name: [MetricQuery(**q) for q in specs] for name, specs in raw.items()}


def describe(name: str, values: list[float], unit: str) -> str:
    def fmt(v: float) -> str:
        return f"{v:.3g}{unit}"

    return (
        f"{name}: {fmt(values[0])} -> {fmt(values[-1])} over 30m "
        f"(min {fmt(min(values))}, max {fmt(max(values))})."
    )


def _fallback(alert: AlertmanagerAlert) -> list[MetricQuery]:
    expr = parse_qs(urlparse(alert.generatorURL).query).get("g0.expr")
    return [MetricQuery(name="alert expression", query=expr[0])] if expr else []


class MetricsCollector:
    name = "metrics"

    def __init__(
        self, client: httpx.AsyncClient, prometheus_url: str, queries: dict[str, list[MetricQuery]]
    ):
        self.client = client
        self.url = prometheus_url.rstrip("/")
        self.queries = queries

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        specs = self.queries.get(alert.alertname) or _fallback(alert)
        lines = []
        for spec in specs[:MAX_QUERIES]:
            r = await self.client.get(
                f"{self.url}/api/v1/query_range",
                params={
                    "query": Template(spec.query).safe_substitute(alert.labels),
                    "start": (now - WINDOW).timestamp(),
                    "end": now.timestamp(),
                    "step": "60",
                },
            )
            r.raise_for_status()
            for series in r.json()["data"]["result"][:1]:
                values = [float(v) for _, v in series["values"]]
                values = [v for v in values if not math.isnan(v)]
                if values:
                    lines.append(describe(spec.name, values, spec.unit))
        return lines
