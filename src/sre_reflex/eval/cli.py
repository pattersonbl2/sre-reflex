import argparse
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from sre_reflex.config import Settings
from sre_reflex.eval.report import render_report
from sre_reflex.models import build_model
from sre_reflex.questions import QUESTIONS
from sre_reflex.store import EvalRow, ReplayRow, Store

log = logging.getLogger(__name__)


def parse_since(value: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([dh])", value)
    if not m:
        raise ValueError(f"expected e.g. 14d or 12h, got {value!r}")
    n = int(m.group(1))
    return timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n)


def load_fixtures(path: str) -> list[ReplayRow]:
    with open(path) as f:
        return [
            ReplayRow(d["alert_id"], d["state"], d["label"], d["complete"])
            for d in (json.loads(line) for line in f if line.strip())
        ]


async def replay(model_names: list[str], rows: list[ReplayRow], settings: Settings) -> list[EvalRow]:
    out = []
    async with httpx.AsyncClient() as client:
        models = [build_model(n, client, settings) for n in model_names]
        for r in rows:
            for m in models:
                try:
                    answers = await m.decide(r.state, QUESTIONS)
                except Exception:
                    log.warning("model %s failed for alert %s", m.name, r.alert_id, exc_info=True)
                    continue
                a = next(x for x in answers if x.question_id == "actionable")
                out.append(EvalRow(
                    r.alert_id, m.name, float(a.value), r.label, a.latency_ms, a.cost_usd, r.complete
                ))
    return out


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--models", default="openjev,ollama")
    parser.add_argument("--labels", choices=["hand", "all"], default="hand")
    parser.add_argument("--since", default="14d")
    parser.add_argument("--replay", action="store_true", help="re-run stored states")
    parser.add_argument("--fixtures", help="JSONL of states+labels; skips the database")
    parser.add_argument("--out", help="write markdown here instead of stdout")


async def run_eval(args: argparse.Namespace, settings: Settings) -> str:
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.fixtures:
        rows = await replay(models, load_fixtures(args.fixtures), settings)
        return render_report(rows, [], f"sre-reflex eval (fixtures: {args.fixtures})")

    since = datetime.now(UTC) - parse_since(args.since)
    store = await Store.open(settings.database_url)
    try:
        if args.replay:
            rows = await replay(models, await store.replay_rows(since, args.labels), settings)
            answer_rows = []
        else:
            rows = [r for r in await store.eval_rows(since, args.labels) if r.model in models]
            answer_rows = [r for r in await store.answer_rows(since) if r.model in models]
    finally:
        await store.close()
    title = f"sre-reflex eval (last {args.since}, labels={args.labels}{', replay' if args.replay else ''})"
    return render_report(rows, answer_rows, title)
