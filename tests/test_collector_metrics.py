import httpx
import respx

from sre_reflex.collectors.metrics import (
    MetricQuery,
    MetricsCollector,
    describe,
    load_metric_queries,
)

PROM = "http://prom:9090"


def _prom(values):
    return httpx.Response(
        200, json={"status": "success", "data": {"result": [{"metric": {}, "values": values}]}}
    )


def test_describe():
    assert describe("5xx ratio", [0.2, 3.5, 8.1], "%") == (
        "5xx ratio: 0.2% -> 8.1% over 30m (min 0.2%, max 8.1%)."
    )


def test_load_metric_queries(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text("HighErrorRate:\n  - name: 5xx ratio\n    query: up\n    unit: '%'\n")
    assert load_metric_queries(str(f)) == {
        "HighErrorRate": [MetricQuery(name="5xx ratio", query="up", unit="%")]
    }
    assert load_metric_queries("") == {}


@respx.mock
async def test_uses_configured_query_with_label_substitution(alert, now):
    route = respx.route(method="GET", url__startswith=f"{PROM}/api/v1/query_range").mock(
        return_value=_prom([[1, "0.2"], [2, "3.5"], [3, "8.1"]])
    )
    queries = {
        "HighErrorRate": [
            MetricQuery(name="5xx ratio", query='sum(rate(x{namespace="$namespace"}[5m]))', unit="%")
        ]
    }
    async with httpx.AsyncClient() as client:
        lines = await MetricsCollector(client, PROM, queries).collect(alert, now)
    assert lines == ["5xx ratio: 0.2% -> 8.1% over 30m (min 0.2%, max 8.1%)."]
    assert 'namespace="n8n"' in route.calls.last.request.url.params["query"]


@respx.mock
async def test_falls_back_to_alert_expression_and_skips_nan(alert, now):
    route = respx.route(method="GET", url__startswith=f"{PROM}/api/v1/query_range").mock(
        return_value=_prom([[1, "NaN"], [2, "1"], [3, "2"]])
    )
    async with httpx.AsyncClient() as client:
        lines = await MetricsCollector(client, PROM, {}).collect(alert, now)
    assert lines == ["alert expression: 1 -> 2 over 30m (min 1, max 2)."]
    assert route.calls.last.request.url.params["query"] == 'rate(http_requests_total{code=~"5.."}[5m])'


async def test_no_queries_and_no_expression_returns_nothing(alert, now):
    alert.generatorURL = ""
    async with httpx.AsyncClient() as client:
        assert await MetricsCollector(client, PROM, {}).collect(alert, now) == []
