import asyncio

from sre_reflex.collectors.base import run_collector


class Slow:
    name = "slow"

    async def collect(self, alert, now):
        await asyncio.sleep(1)
        return ["never"]


class Broken:
    name = "broken"

    async def collect(self, alert, now):
        raise RuntimeError("boom")


class Good:
    name = "good"

    async def collect(self, alert, now):
        return ["line"]


async def test_success(alert, now):
    r = await run_collector(Good(), alert, now, timeout_s=1)
    assert (r.name, r.lines, r.ok) == ("good", ["line"], True)


async def test_timeout_is_not_ok(alert, now):
    r = await run_collector(Slow(), alert, now, timeout_s=0.01)
    assert (r.name, r.lines, r.ok) == ("slow", [], False)


async def test_exception_is_not_ok(alert, now):
    r = await run_collector(Broken(), alert, now, timeout_s=1)
    assert (r.lines, r.ok) == ([], False)
