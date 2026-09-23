from dataclasses import dataclass
from datetime import datetime
from importlib import resources

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.models.base import Answer
from sre_reflex.state import State

# Latest hand label wins; inferred labels only count in "all" mode when no hand label exists.
_LABEL_CTE = """
WITH lbl AS (
    SELECT DISTINCT ON (alert_id) alert_id, value
    FROM labels
    WHERE source = ANY(%(sources)s)
    ORDER BY alert_id, (source = 'hand') DESC, created_at DESC, id DESC
)
"""
_COMPLETE = (
    "NOT EXISTS (SELECT 1 FROM jsonb_each(s.collectors_ok) e WHERE e.value = 'false'::jsonb)"
)


@dataclass
class EvalRow:
    alert_id: int
    model: str
    p_actionable: float
    label: str
    latency_ms: int
    cost_usd: float
    complete: bool


@dataclass
class ReplayRow:
    alert_id: int
    state: str
    label: str
    complete: bool


@dataclass
class AnswerRow:
    state_id: int
    model: str
    question_id: str
    value: float


def _sources(label_mode: str) -> list[str]:
    return ["hand"] if label_mode == "hand" else ["hand", "inferred"]


class Store:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    @classmethod
    async def open(cls, url: str) -> "Store":
        pool = AsyncConnectionPool(url, min_size=1, max_size=5, open=False)
        await pool.open(wait=True, timeout=10)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT name FROM schema_migrations")
            applied = {row[0] for row in await cur.fetchall()}
            files = sorted(
                (f for f in resources.files("sre_reflex.migrations").iterdir()
                 if f.name.endswith(".sql")),
                key=lambda f: f.name,
            )
            for f in files:
                if f.name in applied:
                    continue
                async with conn.transaction():
                    await conn.execute(f.read_text())
                    await conn.execute(
                        "INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,)
                    )

    async def upsert_alert(self, alert: AlertmanagerAlert) -> tuple[int, bool]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO alerts (fingerprint, alertname, labels, fired_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (fingerprint, fired_at) DO NOTHING "
                "RETURNING id",
                (alert.fingerprint, alert.alertname, Jsonb(alert.labels), alert.startsAt),
            )
            row = await cur.fetchone()
            if row:
                return row[0], True
            cur = await conn.execute(
                "SELECT id FROM alerts WHERE fingerprint = %s AND fired_at = %s",
                (alert.fingerprint, alert.startsAt),
            )
            return (await cur.fetchone())[0], False

    async def resolve_alert(
        self, fingerprint: str, fired_at: datetime, resolved_at: datetime
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE alerts SET resolved_at = %s WHERE fingerprint = %s AND fired_at = %s",
                (resolved_at, fingerprint, fired_at),
            )

    async def insert_state(self, alert_id: int, state: State) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO states (alert_id, text, collectors_ok, token_count) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (alert_id, state.text, Jsonb(state.collectors_ok), state.token_count),
            )
            return (await cur.fetchone())[0]

    async def insert_decisions(
        self, state_id: int, model: str, q_version: int, answers: list[Answer]
    ) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO decisions (state_id, model, question_id, q_version, value, "
                "distribution, latency_ms, cost_usd) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (state_id, model, a.question_id, q_version, str(a.value),
                     Jsonb(a.distribution), a.latency_ms, a.cost_usd)
                    for a in answers
                ],
            )

    async def add_label(
        self, alert_id: int, value: str, source: str, sig: str | None = None
    ) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO labels (alert_id, value, source, sig) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (sig) DO NOTHING RETURNING id",
                (alert_id, value, source, sig),
            )
            return await cur.fetchone() is not None

    async def alerts_to_infer(self, now: datetime) -> list[int]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT a.id FROM alerts a
                WHERE a.resolved_at IS NOT NULL
                  AND a.resolved_at - a.fired_at <= interval '10 minutes'
                  AND a.resolved_at <= %s - interval '24 hours'
                  AND NOT EXISTS (SELECT 1 FROM labels l WHERE l.alert_id = a.id)
                ORDER BY a.id
                """,
                (now,),
            )
            return [row[0] for row in await cur.fetchall()]

    async def eval_rows(self, since: datetime, label_mode: str) -> list[EvalRow]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                _LABEL_CTE + f"""
                SELECT s.alert_id, d.model, d.value::float, lbl.value, d.latency_ms,
                       d.cost_usd::float, {_COMPLETE}
                FROM decisions d
                JOIN states s ON s.id = d.state_id
                JOIN lbl ON lbl.alert_id = s.alert_id
                WHERE d.question_id = 'actionable' AND s.created_at >= %(since)s
                ORDER BY d.id
                """,
                {"sources": _sources(label_mode), "since": since},
            )
            return [EvalRow(*row) for row in await cur.fetchall()]

    async def replay_rows(self, since: datetime, label_mode: str) -> list[ReplayRow]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                _LABEL_CTE + f"""
                SELECT s.alert_id, s.text, lbl.value, {_COMPLETE}
                FROM states s JOIN lbl ON lbl.alert_id = s.alert_id
                WHERE s.created_at >= %(since)s
                ORDER BY s.id
                """,
                {"sources": _sources(label_mode), "since": since},
            )
            return [ReplayRow(*row) for row in await cur.fetchall()]

    async def answer_rows(self, since: datetime) -> list[AnswerRow]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT d.state_id, d.model, d.question_id, d.value::float
                FROM decisions d JOIN states s ON s.id = d.state_id
                WHERE d.question_id IN ('severity', 'self_resolving') AND s.created_at >= %s
                ORDER BY d.id
                """,
                (since,),
            )
            return [AnswerRow(*row) for row in await cur.fetchall()]
