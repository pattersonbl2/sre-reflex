import os
from datetime import UTC, datetime, timedelta

import pytest

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.models.base import Answer
from sre_reflex.state import State
from sre_reflex.store import Store

pytestmark = pytest.mark.db
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
async def store():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    s = await Store.open(url)
    async with s.pool.connection() as conn:
        await conn.execute(
            "DROP TABLE IF EXISTS labels, decisions, states, alerts, schema_migrations CASCADE"
        )
    await s.migrate()
    yield s
    await s.close()


def mk_alert(fp="fp1", start=T0):
    return AlertmanagerAlert(
        status="firing", labels={"alertname": "X"}, startsAt=start, fingerprint=fp
    )


def answers(p):
    return [
        Answer(question_id="actionable", value=p, distribution={"yes": p, "no": 1 - p}, latency_ms=10),
        Answer(question_id="severity", value=2, distribution={"2 minor": 1.0}, latency_ms=10),
    ]


async def test_migrate_is_idempotent(store):
    await store.migrate()


async def test_upsert_alert_dedupes(store):
    a = mk_alert()
    first = await store.upsert_alert(a)
    second = await store.upsert_alert(a)
    assert first[1] is True and second == (first[0], False)


async def test_eval_rows_prefer_latest_hand_label(store):
    alert_id, _ = await store.upsert_alert(mk_alert())
    state_id = await store.insert_state(alert_id, State("txt", {"alert": True, "logs": False}, 1))
    await store.insert_decisions(state_id, "fake", 1, answers(0.8))
    assert await store.add_label(alert_id, "noise", "inferred")
    assert await store.add_label(alert_id, "noise", "hand", sig="s1")
    assert await store.add_label(alert_id, "real", "hand", sig="s2")

    rows = await store.eval_rows(T0 - timedelta(days=30), "all")
    assert len(rows) == 1
    r = rows[0]
    assert (r.model, r.label, r.complete, r.latency_ms) == ("fake", "real", False, 10)
    assert r.p_actionable == pytest.approx(0.8)

    replay = await store.replay_rows(T0 - timedelta(days=30), "hand")
    assert [(x.alert_id, x.state, x.label) for x in replay] == [(alert_id, "txt", "real")]

    ans = await store.answer_rows(T0 - timedelta(days=30))
    assert [(a.model, a.question_id, a.value) for a in ans] == [("fake", "severity", 2.0)]


async def test_hand_mode_ignores_inferred(store):
    alert_id, _ = await store.upsert_alert(mk_alert())
    state_id = await store.insert_state(alert_id, State("txt", {"alert": True}, 1))
    await store.insert_decisions(state_id, "fake", 1, answers(0.1))
    await store.add_label(alert_id, "noise", "inferred")
    assert await store.eval_rows(T0 - timedelta(days=30), "hand") == []


async def test_reused_signature_rejected(store):
    alert_id, _ = await store.upsert_alert(mk_alert())
    assert await store.add_label(alert_id, "real", "hand", sig="same") is True
    assert await store.add_label(alert_id, "noise", "hand", sig="same") is False


async def test_alerts_to_infer_rule(store):
    now = T0 + timedelta(days=2)
    quick_old, _ = await store.upsert_alert(mk_alert("quick_old", T0))
    await store.resolve_alert("quick_old", T0, T0 + timedelta(minutes=5))
    _slow_old, _ = await store.upsert_alert(mk_alert("slow_old", T0))
    await store.resolve_alert("slow_old", T0, T0 + timedelta(minutes=30))
    labelled, _ = await store.upsert_alert(mk_alert("labelled", T0))
    await store.resolve_alert("labelled", T0, T0 + timedelta(minutes=5))
    await store.add_label(labelled, "real", "hand", sig="x")
    recent_start = now - timedelta(hours=1)
    await store.upsert_alert(mk_alert("recent", recent_start))
    await store.resolve_alert("recent", recent_start, recent_start + timedelta(minutes=5))
    await store.upsert_alert(mk_alert("open", T0))

    assert await store.alerts_to_infer(now) == [quick_old]
