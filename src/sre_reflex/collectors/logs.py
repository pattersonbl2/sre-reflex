import re
from datetime import datetime, timedelta

import httpx

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.scrub import scrub

WINDOW = timedelta(minutes=15)
ERROR_FILTER = '|~ "(?i)(error|fatal|panic|exception)"'


def dedupe_lines(lines: list[str], max_lines: int = 25, max_len: int = 200) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = re.sub(r"\d+", "#", line)
        if key in seen:
            continue
        seen.add(key)
        out.append(scrub(line)[:max_len])
        if len(out) >= max_lines:
            break
    return out


class LogsCollector:
    name = "logs"

    def __init__(self, client: httpx.AsyncClient, loki_url: str):
        self.client = client
        self.url = loki_url.rstrip("/")

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        namespace = alert.labels.get("namespace")
        if not namespace:
            return []
        matchers = [f'namespace="{namespace}"']
        if pod := alert.labels.get("pod"):
            matchers.append(f'pod="{pod}"')
        r = await self.client.get(
            f"{self.url}/loki/api/v1/query_range",
            params={
                "query": "{" + ", ".join(matchers) + "} " + ERROR_FILTER,
                "start": str(int((now - WINDOW).timestamp() * 1e9)),
                "end": str(int(now.timestamp() * 1e9)),
                "limit": "200",
                "direction": "backward",
            },
        )
        r.raise_for_status()
        lines = [line for stream in r.json()["data"]["result"] for _, line in stream["values"]]
        return dedupe_lines(lines)
