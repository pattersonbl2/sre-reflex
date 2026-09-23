from datetime import timedelta

import httpx
import respx

from sre_reflex.collectors.history import HistoryCollector, episodes, summarize_history

PROM = "http://prom:9090"


def test_episodes_split_on_gaps():
    assert episodes([0, 60, 120, 600, 660], step=60) == [(0, 120), (600, 660)]


def test_summary_without_history(now):
    assert summarize_history([], now) == "No previous firings of this alert in the last 7 days."


@respx.mock
async def test_counts_firings_and_median_duration(alert, now):
    t_now = now.timestamp()
    t0 = (now - timedelta(days=2)).timestamp()
    t1 = (now - timedelta(days=1)).timestamp()
    values = (
        [[t0 + i * 60, "1"] for i in range(3)]          # 3m episode, resolved
        + [[t1 + i * 60, "1"] for i in range(5)]        # 5m episode, resolved
        + [[t_now - 240 + i * 60, "1"] for i in range(5)]  # current, still firing
    )
    route = respx.route(method="GET", url__startswith=f"{PROM}/api/v1/query_range").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"result": [{"metric": {}, "values": values}]}}
        )
    )
    async with httpx.AsyncClient() as client:
        lines = await HistoryCollector(client, PROM).collect(alert, now)

    assert lines == ["Fired 3 times in 7 days; resolved 2 times, median duration 4m."]
    assert 'alertname="HighErrorRate"' in route.calls.last.request.url.params["query"]
