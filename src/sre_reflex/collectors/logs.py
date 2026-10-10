import logging
import re
from datetime import datetime, timedelta

import httpx

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.collectors.base import COLLECTOR_HTTP_TIMEOUT_S
from sre_reflex.scrub import scrub

log = logging.getLogger(__name__)

# Kubernetes names are DNS labels/subdomains; refuse anything that could break out
# of a LogQL string literal.
SAFE_LABEL_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")

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
        pod = alert.labels.get("pod")
        for label, value in (("namespace", namespace), ("pod", pod)):
            if value is not None and not SAFE_LABEL_VALUE.fullmatch(value):
                log.warning(
                    "skipping Loki query for %s: unsafe %s label value", alert.fingerprint, label
                )
                return []
        matchers = [f'namespace="{namespace}"']
        if pod:
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
            timeout=COLLECTOR_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        lines = [line for stream in r.json()["data"]["result"] for _, line in stream["values"]]
        return dedupe_lines(lines)
