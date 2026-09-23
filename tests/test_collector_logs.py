import httpx
import respx

from sre_reflex.collectors.logs import LogsCollector, dedupe_lines

LOKI = "http://loki:3100"


def test_dedupe_ignores_digits_scrubs_and_truncates():
    lines = ["conn refused attempt 1", "conn refused attempt 2", "x" * 300, "mail a@b.io"]
    assert dedupe_lines(lines, max_lines=25, max_len=200) == [
        "conn refused attempt 1",
        "x" * 200,
        "mail <email>",
    ]


def test_dedupe_caps_line_count():
    assert len(dedupe_lines([f"err {c}" for c in "abcdefgh"], max_lines=3)) == 3


@respx.mock
async def test_queries_loki_for_namespace_and_pod(alert, now):
    body = {
        "status": "success",
        "data": {
            "result": [
                {"stream": {}, "values": [
                    ["1", "ERROR connection refused to 10.0.0.5 attempt 1"],
                    ["2", "ERROR connection refused to 10.0.0.5 attempt 2"],
                ]},
                {"stream": {}, "values": [["3", "panic: token Jho74PK1JBwHBsfuTk2mM8OziS9dP8NPv5x"]]},
            ]
        },
    }
    route = respx.route(method="GET", url__startswith=f"{LOKI}/loki/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with httpx.AsyncClient() as client:
        lines = await LogsCollector(client, LOKI).collect(alert, now)
    assert lines == ["ERROR connection refused to <ip> attempt 1", "panic: token <token>"]
    query = route.calls.last.request.url.params["query"]
    assert query.startswith('{namespace="n8n", pod="n8n-7d9f8c6b5-x2k4p"} |~')


async def test_no_namespace_skips_request(alert, now):
    alert.labels = {"alertname": "NodeDown"}
    async with httpx.AsyncClient() as client:
        assert await LogsCollector(client, LOKI).collect(alert, now) == []
