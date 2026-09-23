from datetime import timedelta
from pathlib import Path

import pytest

from sre_reflex.cli import build_parser
from sre_reflex.config import Settings
from sre_reflex.eval.cli import load_fixtures, parse_since, run_eval

FIXTURES = Path(__file__).parent / "fixtures" / "samples.jsonl"


def test_parse_since():
    assert parse_since("14d") == timedelta(days=14)
    assert parse_since("12h") == timedelta(hours=12)
    with pytest.raises(ValueError):
        parse_since("soon")


def test_load_fixtures():
    rows = load_fixtures(str(FIXTURES))
    assert len(rows) == 20
    assert sum(r.label == "real" for r in rows) == 10


async def test_fixture_eval_with_fake_model():
    args = build_parser().parse_args(["eval", "--models", "fake", "--fixtures", str(FIXTURES)])
    md = await run_eval(args, Settings(_env_file=None))
    assert "| fake | 20 |" in md
    assert "| fake | 18 |" in md
