# sre-reflex v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `sre-reflex` repo: an Alertmanager webhook bot that scores each alert with a decision model (open-jev) and an LLM baseline (Ollama) in shadow mode, stores everything in Postgres, posts scores + label buttons to ntfy, and evaluates models against hand labels.

**Architecture:** Python FastAPI bot (stateless, k8s) → four context collectors → ≤800-token state → `DecisionModel` adapters (openjev over HTTP to a separate GPU model server, ollama over HTTP) → Postgres → ntfy. A separate FastAPI model server wraps `typed_decisions.OpenJev`. An eval CLI replays or reads stored decisions and renders a markdown report.

**Tech Stack:** Python 3.12, uv, FastAPI, httpx, pydantic-settings, psycopg 3 + psycopg-pool, prometheus-client, PyYAML, pytest + pytest-asyncio + respx, Docker, Helm, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-23-sre-reflex-v1-design.md`

**Out of scope for this plan:** deploying into the homelab GitOps repo (ArgoCD app, SealedSecrets, CNPG database, the GPU host model-server stack, tunnel route, alert rules) and creating the GitHub remote. Those get their own plan after this repo is green.

## Global Constraints

- Python `>=3.12`; `.python-version` is `3.12`.
- Package name `sre_reflex`, CLI entry point `sre-reflex`, ntfy topic default `sre-reflex`.
- Shadow mode only: nothing suppresses, reroutes, or edits existing Alertmanager notifications.
- State cap 250 tokens, estimated as `ceil(len(text) / 4)`; trim logs first, then metrics; never trim the alert section. open-jev reads at most 256 state tokens, so the cap keeps both models on identical input.
- Question set `Q_VERSION = 1` with ids `actionable` (noul), `severity` (score, 5 options), `self_resolving` (noul).
- Timeouts: collectors 3 s, openjev 5 s, ollama 30 s.
- Label links: HMAC-SHA256 over `alert_id|value|exp`, 7-day expiry, single use per signature.
- Inferred labels: only `noise`, only for alerts resolved ≤10 min after firing with no label 24 h after resolution.
- Alerts `SreReflexDown` and `SreReflexModelServerUnreachable` are never triaged.
- Public repo contains no homelab IPs, hostnames, or secrets.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Map

```
sre-reflex/
  pyproject.toml  .python-version  .gitignore  .env.example  README.md
  Dockerfile  docker-compose.yml
  config/metric-queries.example.yaml
  src/sre_reflex/
    __init__.py
    config.py        # Settings (env)
    telemetry.py     # Prometheus metrics objects
    alerts.py        # Alertmanager webhook models
    scrub.py         # redact emails/IPs/tokens
    collectors/{__init__,base,alert,history,metrics,logs}.py
    state.py         # State + build_state
    questions.py     # Q_VERSION, QUESTIONS
    models/{__init__,base,fake,openjev,ollama}.py
    migrations/{__init__.py,001_init.sql}
    store.py         # Postgres access + row dataclasses
    signing.py       # HMAC label links
    notify.py        # message formatting + ntfy Notifier
    labels.py        # hand label recording + inference
    pipeline.py      # per-alert triage flow
    app.py           # FastAPI app factory
    cli.py           # serve | migrate | infer-labels | eval
    eval/{__init__,report,cli}.py
  server/
    pyproject.toml  Dockerfile  model_server.py  tests/test_model_server.py
  chart/
    Chart.yaml  values.yaml  templates/{_helpers.tpl,deployment,service,configmap,cronjob,servicemonitor}.yaml
  tests/
    conftest.py  stubs.py
    fixtures/samples.jsonl
    test_*.py
    e2e/test_e2e.py
  .github/workflows/ci.yml
```

---

### Task 1: Project scaffold and settings

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `src/sre_reflex/__init__.py`, `src/sre_reflex/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `sre_reflex.config.Settings` (pydantic-settings) with fields `prometheus_url, loki_url, model_server_url, ollama_url, ollama_model, database_url, ntfy_url, ntfy_topic, ntfy_token, public_label_url, label_hmac_key, metric_queries_file, enabled_models, collector_timeout_s, openjev_timeout_s, ollama_timeout_s, state_token_cap` and property `model_names -> list[str]`.

- [ ] **Step 1: Write project files**

`pyproject.toml`:
```toml
[project]
name = "sre-reflex"
version = "0.1.0"
description = "Alert triage with decision models, in shadow mode"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "psycopg[binary]>=3.2",
  "psycopg-pool>=3.2",
  "prometheus-client>=0.20",
  "pyyaml>=6.0",
]

[project.scripts]
sre-reflex = "sre_reflex.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sre_reflex"]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "respx>=0.21", "ruff>=0.6"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
markers = [
  "db: needs TEST_DATABASE_URL",
  "live: calls real model endpoints (LIVE_MODELS)",
  "e2e: needs the docker compose stack",
]
addopts = "-m 'not db and not live and not e2e'"

[tool.ruff]
line-length = 100
target-version = "py312"
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
dist/
.pytest_cache/
.ruff_cache/
.env
```

`.env.example`:
```
PROMETHEUS_URL=http://prometheus:9090
LOKI_URL=http://loki:3100
MODEL_SERVER_URL=http://model-server:8000
OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=qwen3:8b
DATABASE_URL=postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex
NTFY_URL=https://ntfy.example.com
NTFY_TOPIC=sre-reflex
NTFY_TOKEN=
PUBLIC_LABEL_URL=https://sre-reflex.example.com
LABEL_HMAC_KEY=change-me
METRIC_QUERIES_FILE=config/metric-queries.example.yaml
ENABLED_MODELS=openjev,ollama
```

`src/sre_reflex/__init__.py`: empty file.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:
```python
from sre_reflex.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("ENABLED_MODELS", raising=False)
    s = Settings(_env_file=None)
    assert s.state_token_cap == 800
    assert s.ntfy_topic == "sre-reflex"
    assert s.model_names == ["openjev", "ollama"]
    assert (s.collector_timeout_s, s.openjev_timeout_s, s.ollama_timeout_s) == (3.0, 5.0, 30.0)


def test_model_names_from_env(monkeypatch):
    monkeypatch.setenv("ENABLED_MODELS", "fake, openjev ,")
    assert Settings(_env_file=None).model_names == ["fake", "openjev"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv sync && uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.config'`

- [ ] **Step 4: Implement**

`src/sre_reflex/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    model_server_url: str = "http://model-server:8000"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    database_url: str = "postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex"
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = "sre-reflex"
    ntfy_token: str = ""
    public_label_url: str = "http://localhost:8080"
    label_hmac_key: str = "change-me"
    metric_queries_file: str = ""
    enabled_models: str = "openjev,ollama"
    collector_timeout_s: float = 3.0
    openjev_timeout_s: float = 5.0
    ollama_timeout_s: float = 30.0
    state_token_cap: int = 800

    @property
    def model_names(self) -> list[str]:
        return [m.strip() for m in self.enabled_models.split(",") if m.strip()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check .`
Expected: 2 passed; ruff `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example src tests
git commit -m "feat: project scaffold and settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Decision model types, questions, fake adapter, contract suite

**Files:**
- Create: `src/sre_reflex/models/__init__.py`, `src/sre_reflex/models/base.py`, `src/sre_reflex/models/fake.py`, `src/sre_reflex/questions.py`
- Test: `tests/test_models_base.py`, `tests/test_model_contract.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - `models.base`: `Question(id: str, type: Literal["noul","score","choice"], text: str, options: list[str] = [])`, `Answer(question_id: str, value: float | int | str, distribution: dict[str, float], latency_ms: int, cost_usd: float = 0.0)`, `DecisionModel` protocol (`name: str`, `async decide(state: str, questions: list[Question]) -> list[Answer]`), `ModelError(Exception)`, `build_answer(q, distribution, latency_ms, cost_usd=0.0) -> Answer`, `normalize_options(options: list[str], raw: dict) -> dict[str, float]`, `noul_distribution(p: float) -> dict[str, float]`.
  - Value semantics: noul → `float` P(yes); score → `int` 1-based index of most likely option; choice → `str` most likely option.
  - `models.build_model(name: str, client: httpx.AsyncClient, settings: Settings) -> DecisionModel` (raises `ValueError` for unknown names).
  - `questions.Q_VERSION = 1`, `questions.QUESTIONS: list[Question]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_models_base.py`:
```python
import pytest

from sre_reflex.models.base import Question, build_answer, normalize_options, noul_distribution


def test_noul_answer_value_is_yes_probability():
    q = Question(id="a", type="noul", text="t")
    a = build_answer(q, noul_distribution(0.8), latency_ms=12)
    assert a.value == pytest.approx(0.8)
    assert a.distribution == pytest.approx({"yes": 0.8, "no": 0.2})
    assert a.latency_ms == 12 and a.cost_usd == 0.0


def test_score_answer_value_is_one_based_level():
    q = Question(id="s", type="score", text="t", options=["low", "mid", "high"])
    a = build_answer(q, {"low": 0.1, "mid": 0.7, "high": 0.2}, latency_ms=0)
    assert a.value == 2


def test_choice_answer_value_is_option():
    q = Question(id="c", type="choice", text="t", options=["a", "b"])
    assert build_answer(q, {"a": 0.3, "b": 0.7}, latency_ms=0).value == "b"


def test_noul_distribution_clamps():
    assert noul_distribution(1.4) == {"yes": 1.0, "no": 0.0}
    assert noul_distribution(-0.2) == {"yes": 0.0, "no": 1.0}


def test_normalize_options_fills_missing_and_rescales():
    assert normalize_options(["x", "y", "z"], {"x": 1.0, "y": 3.0}) == {"x": 0.25, "y": 0.75, "z": 0.0}


def test_normalize_options_all_zero_is_uniform():
    assert normalize_options(["x", "y"], {"x": -1}) == {"x": 0.5, "y": 0.5}
```

`tests/test_model_contract.py`:
```python
"""Every DecisionModel adapter must pass this suite.

`fake` always runs. Real adapters run with: LIVE_MODELS=openjev,ollama uv run pytest -m live
"""
import math
import os

import httpx
import pytest

from sre_reflex.config import Settings
from sre_reflex.models import build_model
from sre_reflex.questions import QUESTIONS

STATE = "## alert\nAlert HighErrorRate (severity=warning) on namespace=demo, firing 4m."
LIVE = [m for m in os.environ.get("LIVE_MODELS", "").split(",") if m]


@pytest.fixture(params=["fake", *[pytest.param(m, marks=pytest.mark.live) for m in LIVE]])
async def model(request):
    async with httpx.AsyncClient() as client:
        yield build_model(request.param, client, Settings())


async def test_one_answer_per_question_in_order(model):
    answers = await model.decide(STATE, QUESTIONS)
    assert [a.question_id for a in answers] == [q.id for q in QUESTIONS]


async def test_distributions_sum_to_one(model):
    for a in await model.decide(STATE, QUESTIONS):
        assert math.isclose(sum(a.distribution.values()), 1.0, abs_tol=0.01)


async def test_values_in_range(model):
    for q, a in zip(QUESTIONS, await model.decide(STATE, QUESTIONS)):
        if q.type == "noul":
            assert 0.0 <= a.value <= 1.0
        elif q.type == "score":
            assert 1 <= a.value <= len(q.options)
        else:
            assert a.value in q.options


async def test_latency_and_cost_non_negative(model):
    for a in await model.decide(STATE, QUESTIONS):
        assert a.latency_ms >= 0 and a.cost_usd >= 0


async def test_fake_is_deterministic():
    async with httpx.AsyncClient() as client:
        m = build_model("fake", client, Settings())
        assert await m.decide(STATE, QUESTIONS) == await m.decide(STATE, QUESTIONS)


async def test_unknown_model_raises():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            build_model("nope", client, Settings())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models_base.py tests/test_model_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.models'`

- [ ] **Step 3: Implement**

`src/sre_reflex/models/base.py`:
```python
from typing import Literal, Protocol

from pydantic import BaseModel


class Question(BaseModel):
    id: str
    type: Literal["noul", "score", "choice"]
    text: str
    options: list[str] = []


class Answer(BaseModel):
    question_id: str
    value: float | int | str
    distribution: dict[str, float]
    latency_ms: int
    cost_usd: float = 0.0


class ModelError(Exception):
    """An adapter could not produce answers."""


class DecisionModel(Protocol):
    name: str

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]: ...


def noul_distribution(p: float) -> dict[str, float]:
    p = min(1.0, max(0.0, float(p)))
    return {"yes": p, "no": 1.0 - p}


def normalize_options(options: list[str], raw: dict) -> dict[str, float]:
    values = {o: max(0.0, float(raw.get(o, 0.0))) for o in options}
    total = sum(values.values())
    if total == 0:
        return {o: 1.0 / len(options) for o in options}
    return {o: v / total for o, v in values.items()}


def build_answer(
    q: Question, distribution: dict[str, float], latency_ms: int, cost_usd: float = 0.0
) -> Answer:
    if q.type == "noul":
        value: float | int | str = distribution["yes"]
    else:
        best = max(q.options, key=lambda o: distribution.get(o, 0.0))
        value = q.options.index(best) + 1 if q.type == "score" else best
    return Answer(
        question_id=q.id,
        value=value,
        distribution=distribution,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
```

`src/sre_reflex/models/fake.py`:
```python
import hashlib

from sre_reflex.models.base import Answer, Question, build_answer


class FakeModel:
    """Deterministic answers derived from a hash of the state. For tests and CI."""

    name = "fake"

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        answers = []
        for q in questions:
            options = ["yes", "no"] if q.type == "noul" else q.options
            digest = hashlib.sha256(f"{state}|{q.id}".encode()).digest()
            weights = [b + 1 for b in digest[: len(options)]]
            total = sum(weights)
            distribution = {o: w / total for o, w in zip(options, weights)}
            answers.append(build_answer(q, distribution, latency_ms=0))
        return answers
```

`src/sre_reflex/models/__init__.py`:
```python
import httpx

from sre_reflex.config import Settings
from sre_reflex.models.base import DecisionModel
from sre_reflex.models.fake import FakeModel


def build_model(name: str, client: httpx.AsyncClient, settings: Settings) -> DecisionModel:
    if name == "fake":
        return FakeModel()
    raise ValueError(f"unknown model: {name}")
```

`src/sre_reflex/questions.py`:
```python
from sre_reflex.models.base import Question

# Bump Q_VERSION whenever wording or options change.
Q_VERSION = 1

QUESTIONS = [
    Question(id="actionable", type="noul", text="A human needs to take action on this alert."),
    Question(
        id="severity",
        type="score",
        text="How severe is the impact of this alert?",
        options=["1 cosmetic", "2 minor", "3 degraded", "4 major", "5 outage"],
    ),
    Question(
        id="self_resolving",
        type="noul",
        text="This alert will resolve on its own without intervention.",
    ),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models_base.py tests/test_model_contract.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/models src/sre_reflex/questions.py tests/test_models_base.py tests/test_model_contract.py
git commit -m "feat: decision model interface, v1 questions, fake adapter and contract suite

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Alert types, scrubbing, telemetry, collector runner, alert collector

**Files:**
- Create: `src/sre_reflex/alerts.py`, `src/sre_reflex/scrub.py`, `src/sre_reflex/telemetry.py`, `src/sre_reflex/collectors/__init__.py` (empty), `src/sre_reflex/collectors/base.py`, `src/sre_reflex/collectors/alert.py`, `tests/conftest.py`
- Test: `tests/test_scrub.py`, `tests/test_collector_base.py`, `tests/test_collector_alert.py`

**Interfaces:**
- Produces:
  - `alerts.AlertmanagerAlert(status, labels: dict[str,str], annotations: dict[str,str], startsAt: datetime, endsAt: datetime | None, generatorURL: str, fingerprint: str)` with property `alertname`; `alerts.AlertmanagerWebhook(status: str, alerts: list[AlertmanagerAlert])`.
  - `scrub.scrub(text: str) -> str`.
  - `telemetry`: `ALERTS_PROCESSED`, `ADAPTER_LATENCY{model}`, `ADAPTER_ERRORS{model}`, `COLLECTOR_ERRORS{collector}`, `LABELS{source}`.
  - `collectors.base.CollectorResult(name: str, lines: list[str], ok: bool)`, `Collector` protocol (`name: str`, `async collect(alert, now: datetime) -> list[str]`), `async run_collector(collector, alert, now, timeout_s) -> CollectorResult`.
  - `collectors.alert.AlertCollector()` (name `"alert"`).
  - `tests/conftest.py` fixtures `now` (2026-09-23 12:00 UTC) and `alert` (HighErrorRate, namespace `n8n`, pod `n8n-7d9f8c6b5-x2k4p`, started 11:56, fingerprint `abc123`).

- [ ] **Step 1: Write fixtures and failing tests**

`tests/conftest.py`:
```python
from datetime import datetime, timezone

import pytest

from sre_reflex.alerts import AlertmanagerAlert

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def alert() -> AlertmanagerAlert:
    return AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": "HighErrorRate",
            "severity": "warning",
            "namespace": "n8n",
            "pod": "n8n-7d9f8c6b5-x2k4p",
        },
        annotations={"summary": "n8n 5xx rate above 5%", "description": "Contact ops@example.com"},
        startsAt=datetime(2026, 9, 23, 11, 56, tzinfo=timezone.utc),
        fingerprint="abc123",
        generatorURL=(
            "http://prometheus:9090/graph?g0.expr="
            "rate%28http_requests_total%7Bcode%3D~%225..%22%7D%5B5m%5D%29&g0.tab=1"
        ),
    )
```

`tests/test_scrub.py`:
```python
from sre_reflex.scrub import scrub


def test_redacts_email_ip_and_token():
    text = "user ops@example.com from 10.0.0.5 sent Jho74PK1JBwHBsfuTk2mM8OziS9dP8NPv5x"
    assert scrub(text) == "user <email> from <ip> sent <token>"


def test_keeps_hyphenated_names_and_short_words():
    text = "kube-prometheus-stack-alertmanager pod n8n-7d9f8c6b5-x2k4p restarted"
    assert scrub(text) == text
```

`tests/test_collector_base.py`:
```python
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
```

`tests/test_collector_alert.py`:
```python
from sre_reflex.collectors.alert import AlertCollector


async def test_describes_alert_and_scrubs_annotations(alert, now):
    lines = await AlertCollector().collect(alert, now)
    assert lines == [
        "Alert HighErrorRate (severity=warning) on namespace=n8n, pod=n8n-7d9f8c6b5-x2k4p, "
        "firing 4m.",
        "Summary: n8n 5xx rate above 5%",
        "Description: Contact <email>",
    ]


async def test_handles_no_extra_labels(alert, now):
    alert.labels = {"alertname": "Watchdog"}
    alert.annotations = {}
    lines = await AlertCollector().collect(alert, now)
    assert lines == ["Alert Watchdog (severity=none) on no labels, firing 4m."]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scrub.py tests/test_collector_base.py tests/test_collector_alert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.alerts'`

- [ ] **Step 3: Implement**

`src/sre_reflex/alerts.py`:
```python
from datetime import datetime

from pydantic import BaseModel


class AlertmanagerAlert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str

    @property
    def alertname(self) -> str:
        return self.labels.get("alertname", "unknown")


class AlertmanagerWebhook(BaseModel):
    status: str
    alerts: list[AlertmanagerAlert]
```

`src/sre_reflex/scrub.py`:
```python
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 20+ chars of base64/hex-ish text containing at least one digit and one letter, no hyphens.
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9+/=_]*\d)(?=[A-Za-z0-9+/=_]*[A-Za-z])[A-Za-z0-9+/=_]{20,}")


def scrub(text: str) -> str:
    text = _EMAIL.sub("<email>", text)
    text = _IPV4.sub("<ip>", text)
    return _TOKEN.sub("<token>", text)
```

`src/sre_reflex/telemetry.py`:
```python
from prometheus_client import Counter, Histogram

ALERTS_PROCESSED = Counter("sre_reflex_alerts_processed", "Alerts triaged")
ADAPTER_LATENCY = Histogram(
    "sre_reflex_adapter_latency_seconds", "Decision model call latency", ["model"]
)
ADAPTER_ERRORS = Counter("sre_reflex_adapter_errors", "Decision model failures", ["model"])
COLLECTOR_ERRORS = Counter("sre_reflex_collector_errors", "Collector failures", ["collector"])
LABELS = Counter("sre_reflex_labels", "Labels recorded", ["source"])
```

`src/sre_reflex/collectors/base.py`:
```python
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.telemetry import COLLECTOR_ERRORS

log = logging.getLogger(__name__)


@dataclass
class CollectorResult:
    name: str
    lines: list[str]
    ok: bool


class Collector(Protocol):
    name: str

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]: ...


async def run_collector(
    collector: Collector, alert: AlertmanagerAlert, now: datetime, timeout_s: float
) -> CollectorResult:
    try:
        lines = await asyncio.wait_for(collector.collect(alert, now), timeout_s)
        return CollectorResult(collector.name, lines, True)
    except Exception:
        log.warning("collector %s failed for %s", collector.name, alert.fingerprint, exc_info=True)
        COLLECTOR_ERRORS.labels(collector=collector.name).inc()
        return CollectorResult(collector.name, [], False)
```

`src/sre_reflex/collectors/alert.py`:
```python
from datetime import datetime

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.scrub import scrub

_SKIP_LABELS = {"alertname", "severity", "prometheus"}


class AlertCollector:
    name = "alert"

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        severity = alert.labels.get("severity", "none")
        where = ", ".join(
            f"{k}={v}" for k, v in sorted(alert.labels.items()) if k not in _SKIP_LABELS
        )
        minutes = max(0, int((now - alert.startsAt).total_seconds() // 60))
        lines = [
            f"Alert {alert.alertname} (severity={severity}) on {where or 'no labels'}, "
            f"firing {minutes}m."
        ]
        for key in ("summary", "description"):
            if value := alert.annotations.get(key):
                lines.append(f"{key.capitalize()}: {scrub(value)}")
        return lines
```

`src/sre_reflex/collectors/__init__.py`: empty file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scrub.py tests/test_collector_base.py tests/test_collector_alert.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex tests
git commit -m "feat: alert models, scrubbing, telemetry, collector runner, alert collector

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: History collector

**Files:**
- Create: `src/sre_reflex/collectors/history.py`
- Test: `tests/test_collector_history.py`

**Interfaces:**
- Consumes: `AlertmanagerAlert`, conftest fixtures (Task 3).
- Produces: `HistoryCollector(client: httpx.AsyncClient, prometheus_url: str)` (name `"history"`), `episodes(timestamps: list[float], step: float) -> list[tuple[float, float]]`, `summarize_history(eps: list[tuple[float, float]], now: datetime) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_collector_history.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.collectors.history'`

- [ ] **Step 3: Implement**

`src/sre_reflex/collectors/history.py`:
```python
from datetime import datetime, timedelta
from statistics import median

import httpx

from sre_reflex.alerts import AlertmanagerAlert

STEP_S = 60


def episodes(timestamps: list[float], step: float) -> list[tuple[float, float]]:
    if not timestamps:
        return []
    ts = sorted(timestamps)
    runs = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev > step * 1.5:
            runs.append((start, prev))
            start = t
        prev = t
    runs.append((start, prev))
    return runs


def summarize_history(eps: list[tuple[float, float]], now: datetime) -> str:
    if not eps:
        return "No previous firings of this alert in the last 7 days."
    cutoff = now.timestamp() - 2 * STEP_S
    closed = [(s, e) for s, e in eps if e < cutoff]
    if not closed:
        return f"Fired {len(eps)} times in 7 days; none resolved yet."
    durations = [(e - s) / 60 + 1 for s, e in closed]
    return (
        f"Fired {len(eps)} times in 7 days; resolved {len(closed)} times, "
        f"median duration {median(durations):.0f}m."
    )


class HistoryCollector:
    name = "history"

    def __init__(self, client: httpx.AsyncClient, prometheus_url: str):
        self.client = client
        self.url = prometheus_url.rstrip("/")

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        query = f'ALERTS{{alertname="{alert.alertname}",alertstate="firing"}}'
        r = await self.client.get(
            f"{self.url}/api/v1/query_range",
            params={
                "query": query,
                "start": (now - timedelta(days=7)).timestamp(),
                "end": now.timestamp(),
                "step": str(STEP_S),
            },
        )
        r.raise_for_status()
        eps = []
        for series in r.json()["data"]["result"]:
            eps += episodes([float(ts) for ts, _ in series["values"]], STEP_S)
        return [summarize_history(eps, now)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector_history.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/collectors/history.py tests/test_collector_history.py
git commit -m "feat: history collector summarising 7 days of firings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Metrics collector

**Files:**
- Create: `src/sre_reflex/collectors/metrics.py`, `config/metric-queries.example.yaml`
- Test: `tests/test_collector_metrics.py`

**Interfaces:**
- Consumes: `AlertmanagerAlert`, conftest fixtures.
- Produces: `MetricQuery(name: str, query: str, unit: str = "")`, `load_metric_queries(path: str) -> dict[str, list[MetricQuery]]` (empty dict for `""`), `describe(name, values: list[float], unit) -> str`, `MetricsCollector(client, prometheus_url, queries)` (name `"metrics"`). Queries use `string.Template` `$label` placeholders filled from alert labels; at most 3 queries, first series only, 30 min window.

- [ ] **Step 1: Write the failing test**

`tests/test_collector_metrics.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.collectors.metrics'`

- [ ] **Step 3: Implement**

`src/sre_reflex/collectors/metrics.py`:
```python
import math
from datetime import datetime, timedelta
from string import Template
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from pydantic import BaseModel

from sre_reflex.alerts import AlertmanagerAlert

WINDOW = timedelta(minutes=30)
MAX_QUERIES = 3


class MetricQuery(BaseModel):
    name: str
    query: str
    unit: str = ""


def load_metric_queries(path: str) -> dict[str, list[MetricQuery]]:
    if not path:
        return {}
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return {name: [MetricQuery(**q) for q in specs] for name, specs in raw.items()}


def describe(name: str, values: list[float], unit: str) -> str:
    def fmt(v: float) -> str:
        return f"{v:.3g}{unit}"

    return (
        f"{name}: {fmt(values[0])} -> {fmt(values[-1])} over 30m "
        f"(min {fmt(min(values))}, max {fmt(max(values))})."
    )


def _fallback(alert: AlertmanagerAlert) -> list[MetricQuery]:
    expr = parse_qs(urlparse(alert.generatorURL).query).get("g0.expr")
    return [MetricQuery(name="alert expression", query=expr[0])] if expr else []


class MetricsCollector:
    name = "metrics"

    def __init__(
        self, client: httpx.AsyncClient, prometheus_url: str, queries: dict[str, list[MetricQuery]]
    ):
        self.client = client
        self.url = prometheus_url.rstrip("/")
        self.queries = queries

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]:
        specs = self.queries.get(alert.alertname) or _fallback(alert)
        lines = []
        for spec in specs[:MAX_QUERIES]:
            r = await self.client.get(
                f"{self.url}/api/v1/query_range",
                params={
                    "query": Template(spec.query).safe_substitute(alert.labels),
                    "start": (now - WINDOW).timestamp(),
                    "end": now.timestamp(),
                    "step": "60",
                },
            )
            r.raise_for_status()
            for series in r.json()["data"]["result"][:1]:
                values = [float(v) for _, v in series["values"]]
                values = [v for v in values if not math.isnan(v)]
                if values:
                    lines.append(describe(spec.name, values, spec.unit))
        return lines
```

`config/metric-queries.example.yaml`:
```yaml
# alertname -> up to 3 PromQL queries. $label placeholders are filled from alert labels.
HighErrorRate:
  - name: 5xx ratio
    query: 100 * sum(rate(http_requests_total{namespace="$namespace",code=~"5.."}[5m])) / sum(rate(http_requests_total{namespace="$namespace"}[5m]))
    unit: "%"
KubePodCrashLooping:
  - name: restarts in 15m
    query: increase(kube_pod_container_status_restarts_total{namespace="$namespace",pod="$pod"}[15m])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector_metrics.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/collectors/metrics.py config tests/test_collector_metrics.py
git commit -m "feat: metrics collector with per-alert query map and expression fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Logs collector

**Files:**
- Create: `src/sre_reflex/collectors/logs.py`
- Test: `tests/test_collector_logs.py`

**Interfaces:**
- Consumes: `scrub`, `AlertmanagerAlert`, conftest fixtures.
- Produces: `LogsCollector(client, loki_url)` (name `"logs"`), `dedupe_lines(lines: list[str], max_lines: int = 25, max_len: int = 200) -> list[str]`. Returns `[]` without a request when the alert has no `namespace` label.

- [ ] **Step 1: Write the failing test**

`tests/test_collector_logs.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector_logs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.collectors.logs'`

- [ ] **Step 3: Implement**

`src/sre_reflex/collectors/logs.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector_logs.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/collectors/logs.py tests/test_collector_logs.py
git commit -m "feat: Loki logs collector with dedupe and scrubbing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: State builder

**Files:**
- Create: `src/sre_reflex/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `CollectorResult` (Task 3).
- Produces: `State(text: str, collectors_ok: dict[str, bool], token_count: int)` dataclass, `estimate_tokens(text) -> int`, `build_state(results: list[CollectorResult], cap: int) -> State`. Section order `alert, history, metrics, logs`, each rendered as `## <name>` then its lines; sections joined by a blank line.

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
from sre_reflex.collectors.base import CollectorResult
from sre_reflex.state import build_state, estimate_tokens


def R(name, lines, ok=True):
    return CollectorResult(name, lines, ok)


def test_orders_sections_and_records_collectors():
    s = build_state(
        [R("logs", ["l1"]), R("alert", ["a1"]), R("history", [], ok=False), R("metrics", ["m1"])],
        cap=800,
    )
    assert s.text == "## alert\na1\n\n## metrics\nm1\n\n## logs\nl1"
    assert s.collectors_ok == {"logs": True, "alert": True, "history": False, "metrics": True}
    assert s.token_count == estimate_tokens(s.text)


def test_trims_logs_before_metrics():
    logs = [f"log line {i} " + "x" * 90 for i in range(50)]
    s = build_state([R("alert", ["a1"]), R("metrics", ["m1", "m2"]), R("logs", logs)], cap=200)
    assert s.token_count <= 200
    assert "m1\nm2" in s.text
    assert "log line 0 " in s.text and "log line 49" not in s.text


def test_never_trims_alert_section():
    s = build_state([R("alert", ["a" * 400]), R("metrics", ["m1"]), R("logs", ["l1"])], cap=20)
    assert s.text == "## alert\n" + "a" * 400


def test_estimate_tokens_rounds_up():
    assert estimate_tokens("abcde") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.state'`

- [ ] **Step 3: Implement**

`src/sre_reflex/state.py`:
```python
import math
from dataclasses import dataclass

from sre_reflex.collectors.base import CollectorResult

SECTION_ORDER = ["alert", "history", "metrics", "logs"]
TRIM_ORDER = ["logs", "metrics"]


@dataclass
class State:
    text: str
    collectors_ok: dict[str, bool]
    token_count: int


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def build_state(results: list[CollectorResult], cap: int) -> State:
    by_name = {r.name: r for r in results}
    sections = {n: list(by_name[n].lines) if n in by_name else [] for n in SECTION_ORDER}

    def render() -> str:
        return "\n\n".join(
            f"## {n}\n" + "\n".join(sections[n]) for n in SECTION_ORDER if sections[n]
        )

    text = render()
    for name in TRIM_ORDER:
        while estimate_tokens(text) > cap and sections[name]:
            sections[name].pop()
            text = render()
    return State(text, {r.name: r.ok for r in results}, estimate_tokens(text))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/state.py tests/test_state.py
git commit -m "feat: state builder with token cap and trim order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: openjev adapter

**Files:**
- Create: `src/sre_reflex/models/openjev.py`
- Modify: `src/sre_reflex/models/__init__.py`
- Test: `tests/test_model_openjev.py`

**Interfaces:**
- Consumes: `Question`, `Answer`, `ModelError`, `build_answer` (Task 2).
- Produces: `OpenJevModel(client, base_url, timeout_s)` (name `"openjev"`). Wire contract with the model server (Task 10): `POST {base_url}/decide` body `{"state": str, "questions": [Question.model_dump()]}` → `{"answers": [{"question_id": str, "distribution": {option: float}}]}`; noul distributions use keys `yes`/`no`.

- [ ] **Step 1: Write the failing test**

`tests/test_model_openjev.py`:
```python
import json

import httpx
import pytest
import respx

from sre_reflex.models.base import ModelError
from sre_reflex.models.openjev import OpenJevModel
from sre_reflex.questions import QUESTIONS

MS = "http://ms:8000"
SEVERITY = QUESTIONS[1].options


@respx.mock
async def test_posts_state_and_builds_answers():
    route = respx.post(f"{MS}/decide").mock(
        return_value=httpx.Response(200, json={"answers": [
            {"question_id": "severity", "distribution": {o: float(o.startswith("2")) for o in SEVERITY}},
            {"question_id": "actionable", "distribution": {"yes": 0.3, "no": 0.7}},
            {"question_id": "self_resolving", "distribution": {"yes": 0.9, "no": 0.1}},
        ]})
    )
    async with httpx.AsyncClient() as client:
        answers = await OpenJevModel(client, MS, 5).decide("state text", QUESTIONS)

    assert [a.question_id for a in answers] == ["actionable", "severity", "self_resolving"]
    assert [a.value for a in answers] == [pytest.approx(0.3), 2, pytest.approx(0.9)]
    sent = json.loads(route.calls.last.request.content)
    assert sent["state"] == "state text"
    assert [q["id"] for q in sent["questions"]] == ["actionable", "severity", "self_resolving"]


@respx.mock
async def test_http_error_raises_model_error():
    respx.post(f"{MS}/decide").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OpenJevModel(client, MS, 5).decide("s", QUESTIONS)


@respx.mock
async def test_missing_answer_raises_model_error():
    respx.post(f"{MS}/decide").mock(return_value=httpx.Response(200, json={"answers": []}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OpenJevModel(client, MS, 5).decide("s", QUESTIONS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_model_openjev.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.models.openjev'`

- [ ] **Step 3: Implement**

`src/sre_reflex/models/openjev.py`:
```python
import time

import httpx

from sre_reflex.models.base import Answer, ModelError, Question, build_answer


class OpenJevModel:
    name = "openjev"

    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout_s: float):
        self.client = client
        self.url = base_url.rstrip("/") + "/decide"
        self.timeout_s = timeout_s

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        start = time.perf_counter()
        try:
            r = await self.client.post(
                self.url,
                json={"state": state, "questions": [q.model_dump() for q in questions]},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            by_id = {a["question_id"]: a["distribution"] for a in r.json()["answers"]}
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise ModelError(f"openjev: {e}") from e
        latency_ms = int((time.perf_counter() - start) * 1000)
        missing = [q.id for q in questions if q.id not in by_id]
        if missing:
            raise ModelError(f"openjev: no answer for {missing}")
        return [build_answer(q, by_id[q.id], latency_ms) for q in questions]
```

Replace `src/sre_reflex/models/__init__.py` with:
```python
import httpx

from sre_reflex.config import Settings
from sre_reflex.models.base import DecisionModel
from sre_reflex.models.fake import FakeModel
from sre_reflex.models.openjev import OpenJevModel


def build_model(name: str, client: httpx.AsyncClient, settings: Settings) -> DecisionModel:
    if name == "fake":
        return FakeModel()
    if name == "openjev":
        return OpenJevModel(client, settings.model_server_url, settings.openjev_timeout_s)
    raise ValueError(f"unknown model: {name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_openjev.py tests/test_model_contract.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/models tests/test_model_openjev.py
git commit -m "feat: openjev adapter over the model server HTTP contract

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Ollama baseline adapter

**Files:**
- Create: `src/sre_reflex/models/ollama.py`
- Modify: `src/sre_reflex/models/__init__.py`
- Test: `tests/test_model_ollama.py`

**Interfaces:**
- Consumes: `Question`, `Answer`, `ModelError`, `build_answer`, `normalize_options`, `noul_distribution` (Task 2).
- Produces: `OllamaModel(client, base_url, model, timeout_s)` (name `"ollama"`), `response_schema(questions) -> dict`, `build_prompt(state, questions) -> str`. Calls `POST {base_url}/api/chat` with `stream: false`, `think: false`, `options.temperature: 0`, `format: response_schema(...)`.

- [ ] **Step 1: Write the failing test**

`tests/test_model_ollama.py`:
```python
import json

import httpx
import pytest
import respx

from sre_reflex.models.base import ModelError
from sre_reflex.models.ollama import OllamaModel, response_schema
from sre_reflex.questions import QUESTIONS

OL = "http://ollama:11434"
SEVERITY = QUESTIONS[1].options


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(content)}})


def test_schema_requires_every_question_and_option():
    schema = response_schema(QUESTIONS)
    assert schema["required"] == ["actionable", "severity", "self_resolving"]
    assert schema["properties"]["actionable"] == {"type": "number"}
    assert schema["properties"]["severity"]["required"] == SEVERITY


@respx.mock
async def test_normalises_and_clamps():
    route = respx.post(f"{OL}/api/chat").mock(return_value=_chat({
        "actionable": 1.3,
        "severity": {SEVERITY[0]: 0.5, SEVERITY[2]: 1.5},
        "self_resolving": 0.25,
    }))
    async with httpx.AsyncClient() as client:
        answers = await OllamaModel(client, OL, "qwen3:8b", 30).decide("ctx", QUESTIONS)

    assert answers[0].value == 1.0
    assert answers[1].value == 3
    assert answers[1].distribution[SEVERITY[2]] == pytest.approx(0.75)
    assert answers[2].value == pytest.approx(0.25)
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "qwen3:8b"
    assert sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0}
    assert sent["format"] == response_schema(QUESTIONS)
    assert "ctx" in sent["messages"][1]["content"]


@respx.mock
async def test_invalid_json_raises_model_error():
    respx.post(f"{OL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "not json"}})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OllamaModel(client, OL, "m", 30).decide("s", QUESTIONS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_model_ollama.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.models.ollama'`

- [ ] **Step 3: Implement**

`src/sre_reflex/models/ollama.py`:
```python
import json
import time

import httpx

from sre_reflex.models.base import (
    Answer,
    ModelError,
    Question,
    build_answer,
    normalize_options,
    noul_distribution,
)

SYSTEM = (
    "You are an SRE triaging a Prometheus alert. Answer only with the requested JSON. "
    "Give calibrated probabilities between 0 and 1."
)


def response_schema(questions: list[Question]) -> dict:
    props: dict = {}
    for q in questions:
        if q.type == "noul":
            props[q.id] = {"type": "number"}
        else:
            props[q.id] = {
                "type": "object",
                "properties": {o: {"type": "number"} for o in q.options},
                "required": list(q.options),
            }
    return {"type": "object", "properties": props, "required": [q.id for q in questions]}


def build_prompt(state: str, questions: list[Question]) -> str:
    lines = ["Alert context:", state, "", "Answer every question:"]
    for q in questions:
        if q.type == "noul":
            lines.append(f'- "{q.id}": probability that this statement is true: {q.text}')
        else:
            opts = ", ".join(f'"{o}"' for o in q.options)
            lines.append(
                f'- "{q.id}": {q.text} Give a probability for each option, summing to 1: {opts}'
            )
    return "\n".join(lines)


class OllamaModel:
    name = "ollama"

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, timeout_s: float):
        self.client = client
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout_s = timeout_s

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        start = time.perf_counter()
        try:
            r = await self.client.post(
                self.url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": build_prompt(state, questions)},
                    ],
                    "format": response_schema(questions),
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0},
                },
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            content = json.loads(r.json()["message"]["content"])
            latency_ms = int((time.perf_counter() - start) * 1000)
            answers = []
            for q in questions:
                raw = content[q.id]
                dist = (
                    noul_distribution(raw)
                    if q.type == "noul"
                    else normalize_options(q.options, raw)
                )
                answers.append(build_answer(q, dist, latency_ms))
            return answers
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as e:
            raise ModelError(f"ollama: {e}") from e
```

In `src/sre_reflex/models/__init__.py`, add the import and branch:
```python
from sre_reflex.models.ollama import OllamaModel
```
```python
    if name == "ollama":
        return OllamaModel(
            client, settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s
        )
```
(insert the branch after the `openjev` branch, before the `raise`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_ollama.py tests/test_model_contract.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/models tests/test_model_ollama.py
git commit -m "feat: Ollama LLM baseline adapter with JSON-schema output

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: open-jev model server

**Files:**
- Create: `server/pyproject.toml`, `server/model_server.py`, `server/Dockerfile`, `server/tests/test_model_server.py`

**Interfaces:**
- Produces: HTTP `POST /decide` (contract in Task 8), `GET /healthz`, `GET /metrics`. `create_app(model=None)` — when `model` is None, loads `typed_decisions.open_jev.OpenJev.from_pretrained(OPENJEV_MODEL)` at startup. `to_openjev(q) -> dict`, `from_openjev(q, out) -> dict[str, float]`.
- OpenJev format (from the model card): input `{"type": "noul"|"score"|"choice", "instructions": str, "options": [...]}`; output `{"noul": p}` or `{"score"|"choice": ..., "probabilities": {option: p}, "confidence": c}`.

- [ ] **Step 1: Write project file and failing test**

`server/pyproject.toml`:
```toml
[project]
name = "sre-reflex-model-server"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "prometheus-client>=0.20"]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`server/tests/test_model_server.py`:
```python
import pytest
from fastapi.testclient import TestClient

from model_server import create_app


class FakeOpenJev:
    def __init__(self):
        self.calls = []

    def decide(self, text, questions):
        self.calls.append((text, questions))
        out = []
        for q in questions:
            if q["type"] == "noul":
                out.append({"noul": 0.9})
            else:
                probs = {o: 1.0 for o in q["options"]}
                out.append({q["type"]: q["options"][0], "probabilities": probs, "confidence": 0.5})
        return out


def test_decide_translates_both_ways():
    fake = FakeOpenJev()
    with TestClient(create_app(fake)) as client:
        r = client.post("/decide", json={
            "state": "ctx",
            "questions": [
                {"id": "actionable", "type": "noul", "text": "Needs action."},
                {"id": "severity", "type": "score", "text": "How bad?", "options": ["low", "high"]},
            ],
        })
    assert r.status_code == 200
    answers = r.json()["answers"]
    assert answers[0]["question_id"] == "actionable"
    assert answers[0]["distribution"] == pytest.approx({"yes": 0.9, "no": 0.1})
    assert answers[1]["distribution"] == {"low": 0.5, "high": 0.5}
    text, sent = fake.calls[0]
    assert text == "ctx"
    assert sent == [
        {"type": "noul", "instructions": "Needs action."},
        {"type": "score", "instructions": "How bad?", "options": ["low", "high"]},
    ]


def test_healthz_and_metrics():
    with TestClient(create_app(FakeOpenJev())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert "openjev_decide_seconds" in client.get("/metrics").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && uv sync && uv run pytest -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_server'`

- [ ] **Step 3: Implement**

`server/model_server.py`:
```python
"""HTTP wrapper around open-jev (typed_decisions.OpenJev)."""
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from pydantic import BaseModel

MODEL_ID = os.environ.get("OPENJEV_MODEL", "com-kotobalabs/open-jev-deberta-v3-large")
DECIDE_SECONDS = Histogram("openjev_decide_seconds", "open-jev decide latency")


class Question(BaseModel):
    id: str
    type: Literal["noul", "score", "choice"]
    text: str
    options: list[str] = []


class DecideRequest(BaseModel):
    state: str
    questions: list[Question]


class AnswerOut(BaseModel):
    question_id: str
    distribution: dict[str, float]


class DecideResponse(BaseModel):
    answers: list[AnswerOut]


def to_openjev(q: Question) -> dict:
    d: dict = {"type": q.type, "instructions": q.text}
    if q.type != "noul":
        d["options"] = q.options
    return d


def from_openjev(q: Question, out: dict) -> dict[str, float]:
    if q.type == "noul":
        p = min(1.0, max(0.0, float(out["noul"])))
        return {"yes": p, "no": 1.0 - p}
    probs = {o: max(0.0, float(out["probabilities"].get(o, 0.0))) for o in q.options}
    total = sum(probs.values())
    if total == 0:
        return {o: 1.0 / len(q.options) for o in q.options}
    return {o: v / total for o, v in probs.items()}


def load_model():
    import torch
    from typed_decisions.open_jev import OpenJev

    model = OpenJev.from_pretrained(MODEL_ID)
    if torch.cuda.is_available() and hasattr(model, "to"):
        model = model.to("cuda")
    return model


def create_app(model=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.model = model if model is not None else load_model()
        yield

    app = FastAPI(title="open-jev model server", lifespan=lifespan)

    @app.post("/decide")
    def decide(req: DecideRequest, request: Request) -> DecideResponse:
        with DECIDE_SECONDS.time():
            outs = request.app.state.model.decide(
                req.state, [to_openjev(q) for q in req.questions]
            )
        return DecideResponse(answers=[
            AnswerOut(question_id=q.id, distribution=from_openjev(q, o))
            for q, o in zip(req.questions, outs)
        ])

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
```

`server/Dockerfile`:
```dockerfile
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "prometheus-client>=0.20" \
    "transformers>=4.44" safetensors huggingface_hub sentencepiece protobuf \
    "typed-decisions @ git+https://github.com/kotoba-lang/typed-decisions"
COPY model_server.py .
ENV HF_HOME=/models
EXPOSE 8000
CMD ["uvicorn", "model_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest -v`
Expected: 2 passed

- [ ] **Step 5: Pin typed-decisions and smoke-test the real model on this Mac (CPU)**

Run: `git ls-remote https://github.com/kotoba-lang/typed-decisions HEAD`
Replace `git+https://github.com/kotoba-lang/typed-decisions` in `server/Dockerfile` with `git+https://github.com/kotoba-lang/typed-decisions@<sha from output>`.

Run:
```bash
cd server && uv run --with torch --with transformers --with safetensors --with huggingface_hub \
  --with sentencepiece --with protobuf \
  --with "typed-decisions @ git+https://github.com/kotoba-lang/typed-decisions" \
  uvicorn model_server:app --port 8000
```
In another shell:
```bash
curl -s localhost:8000/decide -H 'content-type: application/json' -d '{"state":"Alert HighErrorRate firing 4m on namespace demo","questions":[{"id":"actionable","type":"noul","text":"A human needs to take action on this alert."},{"id":"severity","type":"score","text":"How severe is the impact of this alert?","options":["1 cosmetic","2 minor","3 degraded","4 major","5 outage"]}]}'
```
Expected: JSON with two answers; `actionable` has `yes`/`no` keys summing to 1; `severity` has all five options. If the real output keys differ from the model card (`noul` / `probabilities`), adjust `from_openjev` and add a test case capturing the real shape before continuing. Then stop the server and run the contract suite live:
`MODEL_SERVER_URL=http://localhost:8000 LIVE_MODELS=openjev uv run pytest -m live -v` (from repo root, with the server running). Expected: 4 passed for `openjev`.

- [ ] **Step 6: Commit**

```bash
git add server
git commit -m "feat: open-jev model server with /decide, /healthz, /metrics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Postgres store, migrations, test stubs

**Files:**
- Create: `src/sre_reflex/migrations/__init__.py` (empty), `src/sre_reflex/migrations/001_init.sql`, `src/sre_reflex/store.py`, `tests/stubs.py`, `docker-compose.yml` (postgres-test service only for now)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `AlertmanagerAlert`, `State`, `Answer`.
- Produces:
  - `Store.open(url) -> Store`, `close()`, `migrate()`, `upsert_alert(alert) -> tuple[int, bool]`, `resolve_alert(fingerprint, fired_at, resolved_at)`, `insert_state(alert_id, state) -> int`, `insert_decisions(state_id, model, q_version, answers)`, `add_label(alert_id, value, source, sig=None) -> bool` (False when `sig` reused), `alerts_to_infer(now) -> list[int]`, `eval_rows(since, label_mode) -> list[EvalRow]`, `replay_rows(since, label_mode) -> list[ReplayRow]`, `answer_rows(since) -> list[AnswerRow]`.
  - Dataclasses: `EvalRow(alert_id, model, p_actionable: float, label: str, latency_ms: int, cost_usd: float, complete: bool)`, `ReplayRow(alert_id, state: str, label: str, complete: bool)`, `AnswerRow(state_id, model, question_id, value: float)`.
  - `label_mode`: `"hand"` (hand labels only) or `"all"` (hand preferred, else inferred).
  - `tests/stubs.py`: `MemoryStore` (same write methods, `fail` flag, `to_infer` list), `RecordingNotifier`, `StaticCollector(name, lines)`, `BrokenModel`, `RecordingPipeline`.

- [ ] **Step 1: Add a test database service**

`docker-compose.yml`:
```yaml
services:
  postgres-test:
    image: postgres:16.4
    environment:
      POSTGRES_USER: sre_reflex
      POSTGRES_PASSWORD: sre_reflex
      POSTGRES_DB: sre_reflex_test
    ports: ["5433:5432"]
```

Run: `docker compose up -d postgres-test`
Expected: container `postgres-test` running.

- [ ] **Step 2: Write the failing test**

`tests/test_store.py`:
```python
import os
from datetime import datetime, timedelta, timezone

import pytest

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.models.base import Answer
from sre_reflex.state import State
from sre_reflex.store import Store

pytestmark = pytest.mark.db
T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


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
    slow_old, _ = await store.upsert_alert(mk_alert("slow_old", T0))
    await store.resolve_alert("slow_old", T0, T0 + timedelta(minutes=30))
    labelled, _ = await store.upsert_alert(mk_alert("labelled", T0))
    await store.resolve_alert("labelled", T0, T0 + timedelta(minutes=5))
    await store.add_label(labelled, "real", "hand", sig="x")
    recent_start = now - timedelta(hours=1)
    await store.upsert_alert(mk_alert("recent", recent_start))
    await store.resolve_alert("recent", recent_start, recent_start + timedelta(minutes=5))
    await store.upsert_alert(mk_alert("open", T0))

    assert await store.alerts_to_infer(now) == [quick_old]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `TEST_DATABASE_URL=postgresql://sre_reflex:sre_reflex@localhost:5433/sre_reflex_test uv run pytest -m db tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.store'`

- [ ] **Step 4: Implement**

`src/sre_reflex/migrations/001_init.sql`:
```sql
CREATE TABLE alerts (
    id bigserial PRIMARY KEY,
    fingerprint text NOT NULL,
    alertname text NOT NULL,
    labels jsonb NOT NULL,
    fired_at timestamptz NOT NULL,
    resolved_at timestamptz,
    UNIQUE (fingerprint, fired_at)
);

CREATE TABLE states (
    id bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES alerts(id),
    text text NOT NULL,
    collectors_ok jsonb NOT NULL,
    token_count int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE decisions (
    id bigserial PRIMARY KEY,
    state_id bigint NOT NULL REFERENCES states(id),
    model text NOT NULL,
    question_id text NOT NULL,
    q_version int NOT NULL,
    value text NOT NULL,
    distribution jsonb NOT NULL,
    latency_ms int NOT NULL,
    cost_usd numeric NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE labels (
    id bigserial PRIMARY KEY,
    alert_id bigint NOT NULL REFERENCES alerts(id),
    value text NOT NULL CHECK (value IN ('real', 'noise')),
    source text NOT NULL CHECK (source IN ('hand', 'inferred')),
    sig text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX decisions_state_id_idx ON decisions (state_id);
CREATE INDEX labels_alert_id_idx ON labels (alert_id);
```

`src/sre_reflex/store.py`:
```python
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
```

Note: `eval_rows`, `replay_rows`, `answer_rows` filter on `states.created_at` (insert time), which is `now()`; the test uses `since = T0 - 30 days`, so rows created today are included.

`tests/stubs.py`:
```python
"""In-memory stand-ins shared by pipeline, labels and app tests."""
from sre_reflex.models.base import ModelError


class MemoryStore:
    def __init__(self):
        self.keys: dict = {}
        self.resolved: dict[int, object] = {}
        self.states: list = []
        self.decisions: list = []
        self.labels: list = []
        self.sigs: set[str] = set()
        self.to_infer: list[int] = []
        self.fail = False

    async def upsert_alert(self, alert):
        if self.fail:
            raise RuntimeError("db down")
        key = (alert.fingerprint, alert.startsAt)
        if key in self.keys:
            return self.keys[key], False
        self.keys[key] = len(self.keys) + 1
        return self.keys[key], True

    async def resolve_alert(self, fingerprint, fired_at, resolved_at):
        alert_id = self.keys.get((fingerprint, fired_at))
        if alert_id:
            self.resolved[alert_id] = resolved_at

    async def insert_state(self, alert_id, state):
        self.states.append((alert_id, state))
        return len(self.states)

    async def insert_decisions(self, state_id, model, q_version, answers):
        self.decisions.append((state_id, model, q_version, answers))

    async def add_label(self, alert_id, value, source, sig=None):
        if sig is not None and sig in self.sigs:
            return False
        if sig is not None:
            self.sigs.add(sig)
        self.labels.append((alert_id, value, source))
        return True

    async def alerts_to_infer(self, now):
        return list(self.to_infer)


class RecordingNotifier:
    def __init__(self):
        self.sent: list = []

    async def send(self, title, body, actions=None):
        self.sent.append((title, body, actions))


class StaticCollector:
    def __init__(self, name, lines):
        self.name = name
        self.lines = lines

    async def collect(self, alert, now):
        return list(self.lines)


class BrokenModel:
    name = "broken"

    async def decide(self, state, questions):
        raise ModelError("down")


class RecordingPipeline:
    def __init__(self):
        self.webhooks: list = []

    async def handle(self, webhook, now=None):
        self.webhooks.append(webhook)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `TEST_DATABASE_URL=postgresql://sre_reflex:sre_reflex@localhost:5433/sre_reflex_test uv run pytest -m db -v`
Expected: 6 passed

Run: `uv run pytest -v`
Expected: all unit tests pass; db tests deselected.

- [ ] **Step 6: Commit**

```bash
git add src/sre_reflex/migrations src/sre_reflex/store.py tests/stubs.py tests/test_store.py docker-compose.yml
git commit -m "feat: Postgres store, migrations and in-memory test stubs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Signed label links and ntfy notifier

**Files:**
- Create: `src/sre_reflex/signing.py`, `src/sre_reflex/notify.py`
- Test: `tests/test_signing.py`, `tests/test_notify.py`

**Interfaces:**
- Consumes: `AlertmanagerAlert`, `Answer`.
- Produces:
  - `signing`: `LABEL_TTL = timedelta(days=7)`, `sign(key, alert_id, value, exp) -> str`, `verify(key, alert_id, value, exp, sig, now) -> bool`, `label_url(base_url, key, alert_id, value, now) -> str` (query params `a`, `v`, `exp`, `sig`).
  - `notify`: `format_answers(answers) -> str`, `format_message(alert, answers_by_model: dict[str, list[Answer] | None], collectors_ok: dict[str, bool]) -> tuple[str, str]` (first model in dict = primary), `label_actions(base_url, key, alert_id, now) -> list[dict]`, `Notifier(client, url, topic, token="")` with `async send(title, body, actions=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_signing.py`:
```python
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from sre_reflex.signing import label_url, sign, verify


def test_roundtrip(now):
    exp = int((now + timedelta(days=1)).timestamp())
    assert verify("k", 7, "real", exp, sign("k", 7, "real", exp), now)


def test_rejects_tampered_value_key_and_expired(now):
    exp = int((now + timedelta(days=1)).timestamp())
    sig = sign("k", 7, "real", exp)
    assert not verify("k", 7, "noise", exp, sig, now)
    assert not verify("other", 7, "real", exp, sig, now)
    past = int((now - timedelta(seconds=1)).timestamp())
    assert not verify("k", 7, "real", past, sign("k", 7, "real", past), now)


def test_label_url_expires_in_seven_days(now):
    url = label_url("https://x.example/", "k", 7, "noise", now)
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.path == "/label"
    assert (q["a"], q["v"]) == ("7", "noise")
    assert int(q["exp"]) == int((now + timedelta(days=7)).timestamp())
    assert verify("k", 7, "noise", int(q["exp"]), q["sig"], now)
```

`tests/test_notify.py`:
```python
import json

import httpx
import respx

from sre_reflex.models.base import Answer
from sre_reflex.notify import Notifier, format_message, label_actions


def ans(p_act, sev, p_self):
    return [
        Answer(question_id="actionable", value=p_act, distribution={}, latency_ms=1),
        Answer(question_id="severity", value=sev, distribution={}, latency_ms=1),
        Answer(question_id="self_resolving", value=p_self, distribution={}, latency_ms=1),
    ]


def test_format_message(alert):
    title, body = format_message(
        alert,
        {"openjev": ans(0.22, 2, 0.87), "ollama": ans(0.35, 2, 0.70), "jev": None},
        {"alert": True, "history": True, "metrics": True, "logs": False},
    )
    assert title == "HighErrorRate · n8n"
    assert body.splitlines() == [
        "actionable 0.22 · severity 2/5 · self-resolving 0.87   (openjev)",
        "ollama: actionable 0.35 · severity 2/5 · self-resolving 0.70",
        "jev: unavailable",
        "missing: logs",
    ]


def test_label_actions(now):
    actions = label_actions("https://x.example", "k", 5, now)
    assert [a["label"] for a in actions] == ["✅ Real", "🔇 Noise"]
    assert all(a["action"] == "http" and a["method"] == "POST" for a in actions)
    assert "v=real" in actions[0]["url"] and "a=5" in actions[0]["url"]


@respx.mock
async def test_notifier_posts_json_with_token():
    route = respx.post("https://ntfy.example/").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await Notifier(client, "https://ntfy.example", "sre-reflex", "tok").send("t", "b", [{"x": 1}])
    req = route.calls.last.request
    assert json.loads(req.content) == {
        "topic": "sre-reflex", "title": "t", "message": "b", "actions": [{"x": 1}]
    }
    assert req.headers["authorization"] == "Bearer tok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_signing.py tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.signing'`

- [ ] **Step 3: Implement**

`src/sre_reflex/signing.py`:
```python
import hashlib
import hmac
from datetime import datetime, timedelta
from urllib.parse import urlencode

LABEL_TTL = timedelta(days=7)


def sign(key: str, alert_id: int, value: str, exp: int) -> str:
    msg = f"{alert_id}|{value}|{exp}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def verify(key: str, alert_id: int, value: str, exp: int, sig: str, now: datetime) -> bool:
    if exp < int(now.timestamp()):
        return False
    return hmac.compare_digest(sign(key, alert_id, value, exp), sig)


def label_url(base_url: str, key: str, alert_id: int, value: str, now: datetime) -> str:
    exp = int((now + LABEL_TTL).timestamp())
    query = urlencode({"a": alert_id, "v": value, "exp": exp, "sig": sign(key, alert_id, value, exp)})
    return f"{base_url.rstrip('/')}/label?{query}"
```

`src/sre_reflex/notify.py`:
```python
from datetime import datetime

import httpx

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.models.base import Answer
from sre_reflex.signing import label_url


def format_answers(answers: list[Answer]) -> str:
    by_id = {a.question_id: a.value for a in answers}
    parts = []
    if "actionable" in by_id:
        parts.append(f"actionable {by_id['actionable']:.2f}")
    if "severity" in by_id:
        parts.append(f"severity {by_id['severity']}/5")
    if "self_resolving" in by_id:
        parts.append(f"self-resolving {by_id['self_resolving']:.2f}")
    return " · ".join(parts)


def format_message(
    alert: AlertmanagerAlert,
    answers_by_model: dict[str, list[Answer] | None],
    collectors_ok: dict[str, bool],
) -> tuple[str, str]:
    where = alert.labels.get("namespace") or alert.labels.get("instance") or "cluster"
    lines = []
    for i, (model, answers) in enumerate(answers_by_model.items()):
        if answers is None:
            lines.append(f"{model}: unavailable")
        elif i == 0:
            lines.append(f"{format_answers(answers)}   ({model})")
        else:
            lines.append(f"{model}: {format_answers(answers)}")
    missing = [name for name, ok in collectors_ok.items() if not ok]
    if missing:
        lines.append("missing: " + ", ".join(missing))
    return f"{alert.alertname} · {where}", "\n".join(lines)


def label_actions(base_url: str, key: str, alert_id: int, now: datetime) -> list[dict]:
    return [
        {"action": "http", "label": label, "method": "POST", "clear": True,
         "url": label_url(base_url, key, alert_id, value, now)}
        for label, value in (("✅ Real", "real"), ("🔇 Noise", "noise"))
    ]


class Notifier:
    def __init__(self, client: httpx.AsyncClient, url: str, topic: str, token: str = ""):
        self.client = client
        self.url = url.rstrip("/") + "/"
        self.topic = topic
        self.token = token

    async def send(self, title: str, body: str, actions: list[dict] | None = None) -> None:
        payload: dict = {"topic": self.topic, "title": title, "message": body}
        if actions:
            payload["actions"] = actions
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = await self.client.post(self.url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_signing.py tests/test_notify.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/signing.py src/sre_reflex/notify.py tests/test_signing.py tests/test_notify.py
git commit -m "feat: HMAC label links and ntfy notifier

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Label recording and inference

**Files:**
- Create: `src/sre_reflex/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: `verify` (Task 12), store `add_label`/`alerts_to_infer` (Task 11), `LABELS` counter.
- Produces: `LabelResult` enum (`OK`, `INVALID`, `REUSED`), `async record_hand_label(store, key, alert_id, value, exp, sig, now) -> LabelResult`, `async infer_labels(store, now) -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_labels.py`:
```python
from datetime import timedelta

from sre_reflex.labels import LabelResult, infer_labels, record_hand_label
from sre_reflex.signing import sign
from stubs import MemoryStore


async def test_valid_label_recorded_once(now):
    store = MemoryStore()
    exp = int((now + timedelta(days=1)).timestamp())
    sig = sign("k", 3, "real", exp)
    assert await record_hand_label(store, "k", 3, "real", exp, sig, now) is LabelResult.OK
    assert await record_hand_label(store, "k", 3, "real", exp, sig, now) is LabelResult.REUSED
    assert store.labels == [(3, "real", "hand")]


async def test_bad_signature_or_value_invalid(now):
    store = MemoryStore()
    exp = int((now + timedelta(days=1)).timestamp())
    assert await record_hand_label(store, "k", 3, "real", exp, "bad", now) is LabelResult.INVALID
    sig = sign("k", 3, "maybe", exp)
    assert await record_hand_label(store, "k", 3, "maybe", exp, sig, now) is LabelResult.INVALID
    assert store.labels == []


async def test_infer_labels_marks_noise(now):
    store = MemoryStore()
    store.to_infer = [4, 9]
    assert await infer_labels(store, now) == 2
    assert store.labels == [(4, "noise", "inferred"), (9, "noise", "inferred")]
```

Add to `pyproject.toml` under `[tool.pytest.ini_options]` so tests can `import stubs`:
```toml
pythonpath = ["tests"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.labels'`

- [ ] **Step 3: Implement**

`src/sre_reflex/labels.py`:
```python
from datetime import datetime
from enum import Enum

from sre_reflex.signing import verify
from sre_reflex.telemetry import LABELS

VALUES = {"real", "noise"}


class LabelResult(Enum):
    OK = "ok"
    INVALID = "invalid"
    REUSED = "reused"


async def record_hand_label(
    store, key: str, alert_id: int, value: str, exp: int, sig: str, now: datetime
) -> LabelResult:
    if value not in VALUES or not verify(key, alert_id, value, exp, sig, now):
        return LabelResult.INVALID
    if not await store.add_label(alert_id, value, "hand", sig):
        return LabelResult.REUSED
    LABELS.labels(source="hand").inc()
    return LabelResult.OK


async def infer_labels(store, now: datetime) -> int:
    ids = await store.alerts_to_infer(now)
    for alert_id in ids:
        await store.add_label(alert_id, "noise", "inferred")
        LABELS.labels(source="inferred").inc()
    return len(ids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_labels.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/sre_reflex/labels.py tests/test_labels.py
git commit -m "feat: hand label recording and noise inference

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Triage pipeline

**Files:**
- Create: `src/sre_reflex/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `run_collector`, `build_state`, `QUESTIONS`, `Q_VERSION`, `format_message`, `label_actions`, telemetry, stubs.
- Produces: `SKIP_ALERTS`, `Pipeline(*, collectors, models, store, notifier, label_base_url, label_key, collector_timeout_s=3.0, token_cap=800)` with `async handle(webhook, now=None)` and `async handle_alert(alert, now)`.

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline.py`:
```python
from sre_reflex.alerts import AlertmanagerWebhook
from sre_reflex.models.fake import FakeModel
from sre_reflex.pipeline import Pipeline
from stubs import BrokenModel, MemoryStore, RecordingNotifier, StaticCollector


def make(models=None, store=None):
    store = store or MemoryStore()
    notifier = RecordingNotifier()
    pipeline = Pipeline(
        collectors=[StaticCollector("alert", ["a1"]), StaticCollector("logs", ["l1"])],
        models=models or [FakeModel()],
        store=store,
        notifier=notifier,
        label_base_url="https://x.example",
        label_key="k",
    )
    return pipeline, store, notifier


async def test_firing_alert_is_stored_and_notified(alert, now):
    pipeline, store, notifier = make()
    await pipeline.handle(AlertmanagerWebhook(status="firing", alerts=[alert]), now)
    assert store.states[0][1].text == "## alert\na1\n\n## logs\nl1"
    assert [(d[1], d[2], len(d[3])) for d in store.decisions] == [("fake", 1, 3)]
    title, body, actions = notifier.sent[0]
    assert title == "HighErrorRate · n8n"
    assert body.endswith("(fake)")
    assert len(actions) == 2 and "a=1" in actions[0]["url"]


async def test_duplicate_delivery_is_ignored(alert, now):
    pipeline, _, notifier = make()
    await pipeline.handle_alert(alert, now)
    await pipeline.handle_alert(alert, now)
    assert len(notifier.sent) == 1


async def test_resolved_alert_updates_store_only(alert, now):
    pipeline, store, notifier = make()
    await pipeline.handle_alert(alert, now)
    alert.status = "resolved"
    alert.endsAt = now
    await pipeline.handle_alert(alert, now)
    assert store.resolved == {1: now}
    assert len(notifier.sent) == 1


async def test_self_alerts_are_skipped(alert, now):
    pipeline, store, notifier = make()
    alert.labels["alertname"] = "SreReflexDown"
    await pipeline.handle_alert(alert, now)
    assert notifier.sent == [] and store.keys == {}


async def test_store_down_still_notifies_without_buttons(alert, now):
    store = MemoryStore()
    store.fail = True
    pipeline, _, notifier = make(store=store)
    await pipeline.handle_alert(alert, now)
    assert notifier.sent[0][2] is None


async def test_failed_model_marked_unavailable(alert, now):
    pipeline, store, notifier = make(models=[FakeModel(), BrokenModel()])
    await pipeline.handle_alert(alert, now)
    assert "broken: unavailable" in notifier.sent[0][1]
    assert [d[1] for d in store.decisions] == ["fake"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.pipeline'`

- [ ] **Step 3: Implement**

`src/sre_reflex/pipeline.py`:
```python
import asyncio
import logging
import time
from datetime import datetime, timezone

from sre_reflex.alerts import AlertmanagerAlert, AlertmanagerWebhook
from sre_reflex.collectors.base import run_collector
from sre_reflex.models.base import Answer
from sre_reflex.notify import format_message, label_actions
from sre_reflex.questions import Q_VERSION, QUESTIONS
from sre_reflex.state import build_state
from sre_reflex.telemetry import ADAPTER_ERRORS, ADAPTER_LATENCY, ALERTS_PROCESSED

log = logging.getLogger(__name__)

SKIP_ALERTS = frozenset({"SreReflexDown", "SreReflexModelServerUnreachable"})


class Pipeline:
    def __init__(
        self,
        *,
        collectors,
        models,
        store,
        notifier,
        label_base_url: str,
        label_key: str,
        collector_timeout_s: float = 3.0,
        token_cap: int = 800,
    ):
        self.collectors = collectors
        self.models = models
        self.store = store
        self.notifier = notifier
        self.label_base_url = label_base_url
        self.label_key = label_key
        self.collector_timeout_s = collector_timeout_s
        self.token_cap = token_cap

    async def handle(self, webhook: AlertmanagerWebhook, now: datetime | None = None) -> None:
        for alert in webhook.alerts:
            try:
                await self.handle_alert(alert, now or datetime.now(timezone.utc))
            except Exception:
                log.exception("failed to triage %s", alert.fingerprint)

    async def handle_alert(self, alert: AlertmanagerAlert, now: datetime) -> None:
        if alert.alertname in SKIP_ALERTS:
            return
        if alert.status == "resolved":
            await self.store.resolve_alert(alert.fingerprint, alert.startsAt, alert.endsAt or now)
            return

        alert_id: int | None = None
        try:
            alert_id, created = await self.store.upsert_alert(alert)
            if not created:
                return
        except Exception:
            log.exception("store unavailable; notifying without label buttons")

        results = await asyncio.gather(
            *(run_collector(c, alert, now, self.collector_timeout_s) for c in self.collectors)
        )
        state = build_state(list(results), self.token_cap)
        outputs = await asyncio.gather(*(self._decide(m, state.text) for m in self.models))
        answers_by_model = {m.name: out for m, out in zip(self.models, outputs)}

        if alert_id is not None:
            try:
                state_id = await self.store.insert_state(alert_id, state)
                for name, answers in answers_by_model.items():
                    if answers is not None:
                        await self.store.insert_decisions(state_id, name, Q_VERSION, answers)
            except Exception:
                log.exception("failed to persist decisions for %s", alert.fingerprint)
                alert_id = None

        title, body = format_message(alert, answers_by_model, state.collectors_ok)
        actions = (
            label_actions(self.label_base_url, self.label_key, alert_id, now)
            if alert_id is not None
            else None
        )
        await self.notifier.send(title, body, actions)
        ALERTS_PROCESSED.inc()

    async def _decide(self, model, state_text: str) -> list[Answer] | None:
        start = time.perf_counter()
        try:
            return await model.decide(state_text, QUESTIONS)
        except Exception:
            log.warning("model %s failed", model.name, exc_info=True)
            ADAPTER_ERRORS.labels(model=model.name).inc()
            return None
        finally:
            ADAPTER_LATENCY.labels(model=model.name).observe(time.perf_counter() - start)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/pipeline.py tests/test_pipeline.py
git commit -m "feat: shadow-mode triage pipeline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: HTTP app and CLI

**Files:**
- Create: `src/sre_reflex/app.py`, `src/sre_reflex/cli.py`, `src/sre_reflex/eval/__init__.py` (empty), `src/sre_reflex/eval/cli.py` (stub replaced in Task 16)
- Test: `tests/test_app.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_pipeline(settings, client, store) -> Pipeline`, `create_app(settings=None, *, pipeline=None, store=None) -> FastAPI` with routes `POST /alertmanager` (202), `GET|POST /label?a&v&exp&sig` (200 / 403 / 409), `GET /healthz`, `GET /metrics`. `cli.main(argv=None)` with subcommands `serve [--host --port]`, `migrate`, `infer-labels`, `eval ...` (Task 16). `eval.cli.add_eval_args(parser)` and `async eval.cli.run_eval(args, settings) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:
```python
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from sre_reflex.app import create_app
from sre_reflex.config import Settings
from sre_reflex.signing import sign
from stubs import MemoryStore, RecordingPipeline

WEBHOOK = {
    "version": "4",
    "status": "firing",
    "receiver": "sre-reflex",
    "alerts": [{
        "status": "firing",
        "labels": {"alertname": "X", "namespace": "demo"},
        "annotations": {},
        "startsAt": "2026-09-23T11:56:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "",
        "fingerprint": "f1",
    }],
}


def client_for(pipeline=None, store=None):
    app = create_app(
        Settings(_env_file=None, label_hmac_key="k"),
        pipeline=pipeline or RecordingPipeline(),
        store=store or MemoryStore(),
    )
    return TestClient(app)


def test_webhook_accepted_and_handled():
    pipeline = RecordingPipeline()
    with client_for(pipeline=pipeline) as c:
        r = c.post("/alertmanager", json=WEBHOOK)
    assert r.status_code == 202
    assert pipeline.webhooks[0].alerts[0].fingerprint == "f1"


def test_label_ok_then_reused_then_tampered():
    store = MemoryStore()
    exp = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    params = {"a": 1, "v": "real", "exp": exp, "sig": sign("k", 1, "real", exp)}
    with client_for(store=store) as c:
        assert c.post("/label", params=params).status_code == 200
        assert c.get("/label", params=params).status_code == 409
        assert c.post("/label", params={**params, "v": "noise"}).status_code == 403
    assert store.labels == [(1, "real", "hand")]


def test_health_and_metrics():
    with client_for() as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert "sre_reflex_alerts_processed_total" in c.get("/metrics").text
```

`tests/test_cli.py`:
```python
import pytest

from sre_reflex.cli import build_parser


def test_parser_subcommands():
    p = build_parser()
    assert p.parse_args(["serve", "--port", "9000"]).port == 9000
    assert p.parse_args(["migrate"]).cmd == "migrate"
    assert p.parse_args(["infer-labels"]).cmd == "infer-labels"
    with pytest.raises(SystemExit):
        p.parse_args([])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.app'`

- [ ] **Step 3: Implement**

`src/sre_reflex/app.py`:
```python
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from sre_reflex.alerts import AlertmanagerWebhook
from sre_reflex.collectors.alert import AlertCollector
from sre_reflex.collectors.history import HistoryCollector
from sre_reflex.collectors.logs import LogsCollector
from sre_reflex.collectors.metrics import MetricsCollector, load_metric_queries
from sre_reflex.config import Settings
from sre_reflex.labels import LabelResult, record_hand_label
from sre_reflex.models import build_model
from sre_reflex.notify import Notifier
from sre_reflex.pipeline import Pipeline
from sre_reflex.store import Store


def build_pipeline(settings: Settings, client: httpx.AsyncClient, store) -> Pipeline:
    return Pipeline(
        collectors=[
            AlertCollector(),
            HistoryCollector(client, settings.prometheus_url),
            MetricsCollector(
                client, settings.prometheus_url, load_metric_queries(settings.metric_queries_file)
            ),
            LogsCollector(client, settings.loki_url),
        ],
        models=[build_model(name, client, settings) for name in settings.model_names],
        store=store,
        notifier=Notifier(client, settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token),
        label_base_url=settings.public_label_url,
        label_key=settings.label_hmac_key,
        collector_timeout_s=settings.collector_timeout_s,
        token_cap=settings.state_token_cap,
    )


def create_app(settings: Settings | None = None, *, pipeline=None, store=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if pipeline is not None:
            app.state.pipeline, app.state.store = pipeline, store
            yield
            return
        async with httpx.AsyncClient() as client:
            db = await Store.open(settings.database_url)
            await db.migrate()
            app.state.store = db
            app.state.pipeline = build_pipeline(settings, client, db)
            try:
                yield
            finally:
                await db.close()

    app = FastAPI(title="sre-reflex", lifespan=lifespan)

    @app.post("/alertmanager", status_code=202)
    async def alertmanager(
        webhook: AlertmanagerWebhook, background: BackgroundTasks, request: Request
    ) -> dict:
        background.add_task(request.app.state.pipeline.handle, webhook)
        return {"status": "accepted"}

    @app.api_route("/label", methods=["GET", "POST"], response_class=PlainTextResponse)
    async def label(request: Request, a: int, v: str, exp: int, sig: str) -> str:
        result = await record_hand_label(
            request.app.state.store, settings.label_hmac_key, a, v, exp, sig,
            datetime.now(timezone.utc),
        )
        if result is LabelResult.INVALID:
            raise HTTPException(403, "invalid or expired link")
        if result is LabelResult.REUSED:
            raise HTTPException(409, "already recorded")
        return f"Recorded: {v}"

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
```

`src/sre_reflex/eval/cli.py` (temporary; Task 16 replaces it):
```python
import argparse

from sre_reflex.config import Settings


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    pass


async def run_eval(args: argparse.Namespace, settings: Settings) -> str:
    raise NotImplementedError("implemented in Task 16")
```

`src/sre_reflex/cli.py`:
```python
import argparse
import asyncio
from datetime import datetime, timezone

from sre_reflex.config import Settings
from sre_reflex.eval.cli import add_eval_args, run_eval
from sre_reflex.labels import infer_labels
from sre_reflex.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sre-reflex")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the webhook/label API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("infer-labels", help="label quick self-resolving alerts as noise")
    add_eval_args(sub.add_parser("eval", help="compare models against labels"))
    return parser


async def _migrate(settings: Settings) -> None:
    store = await Store.open(settings.database_url)
    try:
        await store.migrate()
    finally:
        await store.close()


async def _infer(settings: Settings) -> int:
    store = await Store.open(settings.database_url)
    try:
        return await infer_labels(store, datetime.now(timezone.utc))
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.cmd == "serve":
        import uvicorn

        from sre_reflex.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.cmd == "migrate":
        asyncio.run(_migrate(settings))
    elif args.cmd == "infer-labels":
        print(f"inferred {asyncio.run(_infer(settings))} labels")
    elif args.cmd == "eval":
        report = asyncio.run(run_eval(args, settings))
        if getattr(args, "out", None):
            with open(args.out, "w") as f:
                f.write(report)
        else:
            print(report)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all unit tests pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/app.py src/sre_reflex/cli.py src/sre_reflex/eval tests/test_app.py tests/test_cli.py
git commit -m "feat: FastAPI app (webhook, label, health, metrics) and CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Eval report and CLI

**Files:**
- Create: `src/sre_reflex/eval/report.py`, `tests/fixtures/samples.jsonl`
- Modify: `src/sre_reflex/eval/cli.py` (replace the Task 15 stub)
- Test: `tests/test_eval_report.py`, `tests/test_eval_cli.py`

**Interfaces:**
- Consumes: `EvalRow`, `ReplayRow`, `AnswerRow`, `Store` (Task 11), `build_model` (Task 2), `QUESTIONS`.
- Produces:
  - `report.ModelMetrics(model, n, precision, recall, f1, brier, p50_ms, p95_ms, cost_usd)`, `compute_metrics(rows, threshold=0.5) -> list[ModelMetrics]` (sorted by model), `agreement(rows: list[AnswerRow]) -> dict[str, float | None]`, `render_report(rows, answer_rows, title) -> str`.
  - `eval.cli`: `parse_since(s) -> timedelta` (`14d`, `12h`), `load_fixtures(path) -> list[ReplayRow]`, `async replay(model_names, rows, settings) -> list[EvalRow]`, `add_eval_args`, `run_eval`.
  - CLI: `sre-reflex eval [--models openjev,ollama] [--labels hand|all] [--since 14d] [--replay] [--fixtures PATH] [--out PATH]`.

- [ ] **Step 1: Write fixtures and failing tests**

`tests/fixtures/samples.jsonl` (20 scrubbed samples, 10 real / 10 noise):
```jsonl
{"alert_id": 1, "label": "real", "complete": true, "state": "## alert\nAlert KubePodCrashLooping (severity=warning) on namespace=n8n, pod=n8n-abc, firing 12m.\n\n## history\nNo previous firings of this alert in the last 7 days.\n\n## logs\npanic: runtime error: invalid memory address"}
{"alert_id": 2, "label": "real", "complete": true, "state": "## alert\nAlert TargetDown (severity=critical) on job=gitea, firing 9m.\n\n## history\nNo previous firings of this alert in the last 7 days."}
{"alert_id": 3, "label": "real", "complete": true, "state": "## alert\nAlert HighErrorRate (severity=warning) on namespace=vaultwarden, firing 15m.\n\n## metrics\n5xx ratio: 0.1% -> 22% over 30m (min 0.1%, max 22%)."}
{"alert_id": 4, "label": "real", "complete": true, "state": "## alert\nAlert KubePersistentVolumeFillingUp (severity=critical) on namespace=monitoring, firing 30m.\n\n## metrics\nvolume used: 91% -> 97% over 30m (min 91%, max 97%)."}
{"alert_id": 5, "label": "real", "complete": true, "state": "## alert\nAlert NodeNotReady (severity=critical) on node=worker-2, firing 6m.\n\n## history\nFired 1 times in 7 days; none resolved yet."}
{"alert_id": 6, "label": "real", "complete": false, "state": "## alert\nAlert PostgresDown (severity=critical) on namespace=db, firing 4m."}
{"alert_id": 7, "label": "real", "complete": true, "state": "## alert\nAlert CertificateExpiringSoon (severity=warning) on namespace=traefik, firing 60m.\nSummary: certificate expires in 3 days"}
{"alert_id": 8, "label": "real", "complete": true, "state": "## alert\nAlert BlackboxProbeFailed (severity=critical) on instance=<url>, firing 8m.\n\n## history\nNo previous firings of this alert in the last 7 days."}
{"alert_id": 9, "label": "real", "complete": true, "state": "## alert\nAlert KubeDeploymentReplicasMismatch (severity=warning) on namespace=ntfy, firing 20m.\n\n## logs\nERROR failed to pull image: manifest unknown"}
{"alert_id": 10, "label": "real", "complete": true, "state": "## alert\nAlert LonghornVolumeDegraded (severity=warning) on namespace=longhorn-system, firing 25m.\n\n## history\nFired 2 times in 7 days; resolved 1 times, median duration 40m."}
{"alert_id": 11, "label": "noise", "complete": true, "state": "## alert\nAlert CPUThrottlingHigh (severity=info) on namespace=monitoring, firing 3m.\n\n## history\nFired 41 times in 7 days; resolved 40 times, median duration 4m."}
{"alert_id": 12, "label": "noise", "complete": true, "state": "## alert\nAlert Watchdog (severity=none) on no labels, firing 0m."}
{"alert_id": 13, "label": "noise", "complete": true, "state": "## alert\nAlert KubeJobFailed (severity=warning) on namespace=renovate, firing 2m.\n\n## history\nFired 14 times in 7 days; resolved 14 times, median duration 3m."}
{"alert_id": 14, "label": "noise", "complete": true, "state": "## alert\nAlert HighErrorRate (severity=warning) on namespace=homepage, firing 1m.\n\n## metrics\n5xx ratio: 0% -> 5.1% over 30m (min 0%, max 5.1%).\n\n## history\nFired 22 times in 7 days; resolved 22 times, median duration 2m."}
{"alert_id": 15, "label": "noise", "complete": true, "state": "## alert\nAlert NodeClockNotSynchronising (severity=warning) on instance=<ip>, firing 2m.\n\n## history\nFired 9 times in 7 days; resolved 9 times, median duration 3m."}
{"alert_id": 16, "label": "noise", "complete": true, "state": "## alert\nAlert InfoInhibitor (severity=none) on namespace=kube-system, firing 5m."}
{"alert_id": 17, "label": "noise", "complete": false, "state": "## alert\nAlert KubeletTooManyPods (severity=info) on node=worker-1, firing 3m."}
{"alert_id": 18, "label": "noise", "complete": true, "state": "## alert\nAlert CPUThrottlingHigh (severity=info) on namespace=n8n, firing 4m.\n\n## history\nFired 30 times in 7 days; resolved 30 times, median duration 5m."}
{"alert_id": 19, "label": "noise", "complete": true, "state": "## alert\nAlert BlackboxSlowProbe (severity=warning) on instance=<url>, firing 1m.\n\n## history\nFired 17 times in 7 days; resolved 17 times, median duration 2m."}
{"alert_id": 20, "label": "noise", "complete": true, "state": "## alert\nAlert KubeContainerWaiting (severity=warning) on namespace=renovate, firing 2m.\n\n## logs\nERROR rate limited by registry, retrying"}
```

`tests/test_eval_report.py`:
```python
import pytest

from sre_reflex.eval.report import agreement, compute_metrics, render_report
from sre_reflex.store import AnswerRow, EvalRow


def row(p, label, latency, model="a", complete=True):
    return EvalRow(1, model, p, label, latency, 0.001, complete)


ROWS = [row(0.9, "real", 10), row(0.8, "noise", 20), row(0.2, "real", 30), row(0.1, "noise", 40)]


def test_compute_metrics():
    [m] = compute_metrics(ROWS)
    assert (m.model, m.n) == ("a", 4)
    assert (m.precision, m.recall, m.f1) == (0.5, 0.5, 0.5)
    assert m.brier == pytest.approx(0.325)
    assert (m.p50_ms, m.p95_ms) == (20, 40)
    assert m.cost_usd == pytest.approx(0.004)


def test_no_positive_predictions_gives_zero_precision():
    [m] = compute_metrics([row(0.1, "real", 1)])
    assert (m.precision, m.recall, m.f1) == (0.0, 0.0, 0.0)


def test_agreement():
    rows = [
        AnswerRow(1, "openjev", "severity", 2), AnswerRow(1, "ollama", "severity", 2),
        AnswerRow(2, "openjev", "severity", 3), AnswerRow(2, "ollama", "severity", 4),
        AnswerRow(1, "openjev", "self_resolving", 0.9), AnswerRow(1, "ollama", "self_resolving", 0.6),
    ]
    assert agreement(rows) == {"severity": 0.5, "self_resolving": 1.0}


def test_render_report_sections():
    md = render_report(ROWS + [row(0.9, "real", 5, complete=False)], [], "Test")
    assert md.startswith("# Test")
    assert "## Actionable vs labels (all states)" in md
    assert "## Actionable vs labels (complete context only)" in md
    assert "States with missing collectors: 1" in md
    assert "| a | 5 |" in md and "| a | 4 |" in md
```

`tests/test_eval_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_report.py tests/test_eval_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sre_reflex.eval.report'`

- [ ] **Step 3: Implement**

`src/sre_reflex/eval/report.py`:
```python
import math
from collections import defaultdict
from dataclasses import dataclass

from sre_reflex.store import AnswerRow, EvalRow


@dataclass
class ModelMetrics:
    model: str
    n: int
    precision: float
    recall: float
    f1: float
    brier: float
    p50_ms: int
    p95_ms: int
    cost_usd: float


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def compute_metrics(rows: list[EvalRow], threshold: float = 0.5) -> list[ModelMetrics]:
    by_model: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_model[r.model].append(r)
    out = []
    for model in sorted(by_model):
        rs = by_model[model]
        tp = sum(r.p_actionable >= threshold and r.label == "real" for r in rs)
        fp = sum(r.p_actionable >= threshold and r.label == "noise" for r in rs)
        fn = sum(r.p_actionable < threshold and r.label == "real" for r in rs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        brier = sum((r.p_actionable - (r.label == "real")) ** 2 for r in rs) / len(rs)
        latencies = [r.latency_ms for r in rs]
        out.append(ModelMetrics(
            model, len(rs), precision, recall, f1, brier,
            _percentile(latencies, 0.5), _percentile(latencies, 0.95),
            sum(r.cost_usd for r in rs),
        ))
    return out


def agreement(rows: list[AnswerRow]) -> dict[str, float | None]:
    grouped: dict[str, dict[int, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        grouped[r.question_id][r.state_id][r.model] = r.value
    result: dict[str, float | None] = {}
    for question, states in grouped.items():
        compared = agreed = 0
        for by_model in states.values():
            if len(by_model) < 2:
                continue
            compared += 1
            values = list(by_model.values())
            if question == "severity":
                agreed += len(set(values)) == 1
            else:
                agreed += len({v > 0.5 for v in values}) == 1
        result[question] = agreed / compared if compared else None
    return result


def _table(metrics: list[ModelMetrics]) -> str:
    lines = [
        "| model | n | precision | recall | F1 | Brier | p50 ms | p95 ms | cost $ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.model} | {m.n} | {m.precision:.2f} | {m.recall:.2f} | {m.f1:.2f} | "
            f"{m.brier:.3f} | {m.p50_ms} | {m.p95_ms} | {m.cost_usd:.4f} |"
        )
    return "\n".join(lines)


def render_report(rows: list[EvalRow], answer_rows: list[AnswerRow], title: str) -> str:
    incomplete = len({r.alert_id for r in rows if not r.complete})
    parts = [
        f"# {title}",
        "## Actionable vs labels (all states)",
        _table(compute_metrics(rows)),
        "## Actionable vs labels (complete context only)",
        _table(compute_metrics([r for r in rows if r.complete])),
        f"States with missing collectors: {incomplete}",
    ]
    if answer_rows:
        parts.append("## Inter-model agreement (no ground truth)")
        for question, rate in agreement(answer_rows).items():
            parts.append(f"- {question}: " + ("n/a" if rate is None else f"{rate:.0%}"))
    return "\n\n".join(parts) + "\n"
```

Replace `src/sre_reflex/eval/cli.py` with:
```python
import argparse
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from sre_reflex.config import Settings
from sre_reflex.eval.report import render_report
from sre_reflex.models import build_model
from sre_reflex.questions import QUESTIONS
from sre_reflex.store import EvalRow, ReplayRow, Store


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

    since = datetime.now(timezone.utc) - parse_since(args.since)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v && uv run sre-reflex eval --models fake --fixtures tests/fixtures/samples.jsonl`
Expected: all unit tests pass; the command prints a markdown report containing `| fake | 20 |`.

- [ ] **Step 5: Commit**

```bash
git add src/sre_reflex/eval tests/fixtures tests/test_eval_report.py tests/test_eval_cli.py
git commit -m "feat: eval report (precision/recall/Brier/latency/cost) and CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Container image and end-to-end test

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `tests/e2e/test_e2e.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the whole bot.
- Produces: image running `sre-reflex serve` on port 8080 as UID 65534; compose stack `postgres`, `ntfy`, `bot` (fake model, unreachable Prometheus/Loki) plus existing `postgres-test`.

- [ ] **Step 1: Write the image and stack**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER 65534
EXPOSE 8080
CMD ["sre-reflex", "serve"]
```

`.dockerignore`:
```
.venv
.git
tests
docs
server
chart
```

Replace `docker-compose.yml` with:
```yaml
services:
  postgres-test:
    image: postgres:16.4
    environment:
      POSTGRES_USER: sre_reflex
      POSTGRES_PASSWORD: sre_reflex
      POSTGRES_DB: sre_reflex_test
    ports: ["5433:5432"]

  postgres:
    image: postgres:16.4
    environment:
      POSTGRES_USER: sre_reflex
      POSTGRES_PASSWORD: sre_reflex
      POSTGRES_DB: sre_reflex
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "sre_reflex"]
      interval: 2s
      retries: 20

  ntfy:
    image: binwiederhier/ntfy:v2.11.0
    command: serve
    ports: ["8081:80"]

  bot:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
      ntfy:
        condition: service_started
    environment:
      DATABASE_URL: postgresql://sre_reflex:sre_reflex@postgres:5432/sre_reflex
      ENABLED_MODELS: fake
      NTFY_URL: http://ntfy
      PROMETHEUS_URL: http://unreachable.invalid:9090
      LOKI_URL: http://unreachable.invalid:3100
      PUBLIC_LABEL_URL: http://localhost:8080
      LABEL_HMAC_KEY: e2e-key
    ports: ["8080:8080"]
```

- [ ] **Step 2: Write the e2e test**

`tests/e2e/test_e2e.py`:
```python
import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.e2e
BOT = os.environ.get("E2E_BOT_URL", "http://localhost:8080")
NTFY = os.environ.get("E2E_NTFY_URL", "http://localhost:8081")
DB = os.environ.get("E2E_DATABASE_URL", "postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex")


def test_webhook_to_ntfy_and_database():
    fp = uuid4().hex
    started = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": "4", "status": "firing", "receiver": "sre-reflex",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": f"E2E{fp[:6]}", "severity": "warning", "namespace": "e2e"},
            "annotations": {"summary": "end to end"},
            "startsAt": started, "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "", "fingerprint": fp,
        }],
    }
    assert httpx.post(f"{BOT}/alertmanager", json=payload).status_code == 202

    message = None
    deadline = time.time() + 30
    while time.time() < deadline and message is None:
        r = httpx.get(f"{NTFY}/sre-reflex/json", params={"poll": "1", "since": "all"})
        for line in r.text.splitlines():
            m = json.loads(line)
            if m.get("title", "").startswith(f"E2E{fp[:6]}"):
                message = m
        time.sleep(1)

    assert message is not None, "no ntfy message within 30s"
    assert "(fake)" in message["message"]
    # history and logs hit unreachable hosts; metrics has no query and no expression, so it
    # succeeds with no lines.
    assert "missing: history, logs" in message["message"]
    assert len(message["actions"]) == 2

    with psycopg.connect(DB) as conn:
        count = conn.execute(
            "SELECT count(*) FROM decisions d JOIN states s ON s.id = d.state_id "
            "JOIN alerts a ON a.id = s.alert_id WHERE a.fingerprint = %s",
            (fp,),
        ).fetchone()[0]
    assert count == 3
```

- [ ] **Step 3: Run the stack and the e2e test**

Run: `docker compose up -d --build postgres ntfy bot && uv run pytest -m e2e -v`
Expected: 1 passed. If it fails, check `docker compose logs bot`.

Run: `docker compose down`

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml tests/e2e
git commit -m "feat: container image, compose stack and end-to-end test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Helm chart, CI, README

**Files:**
- Create: `chart/Chart.yaml`, `chart/values.yaml`, `chart/templates/_helpers.tpl`, `chart/templates/deployment.yaml`, `chart/templates/service.yaml`, `chart/templates/configmap.yaml`, `chart/templates/cronjob.yaml`, `chart/templates/servicemonitor.yaml`, `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: image from Task 17 (`sre-reflex serve`, `sre-reflex infer-labels`), `/healthz`, `/metrics`.
- Produces: chart values `image.repository` (required), `image.tag` (defaults to appVersion), `env` (map of non-secret settings), `existingSecret` (Secret with `DATABASE_URL`, `LABEL_HMAC_KEY`, `NTFY_TOKEN`), `metricQueries` (map mounted as `/config/metric-queries.yaml`), `inferLabels.schedule`, `serviceMonitor.enabled`, `serviceMonitor.labels`, `resources`.

- [ ] **Step 1: Write the chart**

`chart/Chart.yaml`:
```yaml
apiVersion: v2
name: sre-reflex
description: Shadow-mode alert triage with decision models
type: application
version: 0.1.0
appVersion: "0.1.0"
```

`chart/values.yaml`:
```yaml
image:
  repository: ""   # required, e.g. ghcr.io/<you>/sre-reflex
  tag: ""          # defaults to Chart.appVersion
  pullPolicy: IfNotPresent

# Non-secret settings, e.g. PROMETHEUS_URL, LOKI_URL, MODEL_SERVER_URL, OLLAMA_URL, NTFY_URL
env: {}

# Name of a Secret providing DATABASE_URL, LABEL_HMAC_KEY and NTFY_TOKEN
existingSecret: ""

# alertname -> list of {name, query, unit}; see config/metric-queries.example.yaml
metricQueries: {}

inferLabels:
  schedule: "17 * * * *"

serviceMonitor:
  enabled: true
  labels: {}

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    memory: 256Mi
```

`chart/templates/_helpers.tpl`:
```
{{- define "sre-reflex.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sre-reflex.selectorLabels" -}}
app.kubernetes.io/name: sre-reflex
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "sre-reflex.labels" -}}
{{ include "sre-reflex.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "sre-reflex.image" -}}
{{ required "image.repository is required" .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "sre-reflex.env" -}}
- name: METRIC_QUERIES_FILE
  value: /config/metric-queries.yaml
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "sre-reflex.envFrom" -}}
{{- with .Values.existingSecret }}
envFrom:
  - secretRef:
      name: {{ . }}
{{- end }}
{{- end -}}
```

`chart/templates/configmap.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "sre-reflex.fullname" . }}
  labels:
    {{- include "sre-reflex.labels" . | nindent 4 }}
data:
  metric-queries.yaml: |
    {{- toYaml .Values.metricQueries | nindent 4 }}
```

`chart/templates/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "sre-reflex.fullname" . }}
  labels:
    {{- include "sre-reflex.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "sre-reflex.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "sre-reflex.selectorLabels" . | nindent 8 }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 65534
      containers:
        - name: bot
          image: {{ include "sre-reflex.image" . }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["sre-reflex", "serve"]
          ports:
            - name: http
              containerPort: 8080
          env:
            {{- include "sre-reflex.env" . | nindent 12 }}
          {{- include "sre-reflex.envFrom" . | nindent 10 }}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
          livenessProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 30
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            - name: config
              mountPath: /config
      volumes:
        - name: config
          configMap:
            name: {{ include "sre-reflex.fullname" . }}
```

`chart/templates/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "sre-reflex.fullname" . }}
  labels:
    {{- include "sre-reflex.labels" . | nindent 4 }}
spec:
  selector:
    {{- include "sre-reflex.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: 8080
      targetPort: http
```

`chart/templates/cronjob.yaml`:
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "sre-reflex.fullname" . }}-infer-labels
  labels:
    {{- include "sre-reflex.labels" . | nindent 4 }}
spec:
  schedule: {{ .Values.inferLabels.schedule | quote }}
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          securityContext:
            runAsNonRoot: true
            runAsUser: 65534
          containers:
            - name: infer-labels
              image: {{ include "sre-reflex.image" . }}
              command: ["sre-reflex", "infer-labels"]
              env:
                {{- include "sre-reflex.env" . | nindent 16 }}
              {{- include "sre-reflex.envFrom" . | nindent 14 }}
```

`chart/templates/servicemonitor.yaml`:
```yaml
{{- if .Values.serviceMonitor.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "sre-reflex.fullname" . }}
  labels:
    {{- include "sre-reflex.labels" . | nindent 4 }}
    {{- with .Values.serviceMonitor.labels }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
spec:
  selector:
    matchLabels:
      {{- include "sre-reflex.selectorLabels" . | nindent 6 }}
  endpoints:
    - port: http
      path: /metrics
      interval: 60s
{{- end }}
```

- [ ] **Step 2: Verify the chart renders**

Run: `helm lint chart --set image.repository=example/sre-reflex && helm template t chart --set image.repository=example/sre-reflex --set existingSecret=s --set env.LOKI_URL=http://loki:3100 | grep -E "image:|secretRef|LOKI_URL|infer-labels"`
Expected: lint `0 chart(s) failed`; output shows `image: example/sre-reflex:0.1.0` twice, a `secretRef` block twice, `LOKI_URL` twice, and the `infer-labels` command.

Run: `helm template t chart 2>&1 | grep "image.repository is required"`
Expected: the error message is printed.

- [ ] **Step 3: Write CI**

`.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  bot:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16.4
        env:
          POSTGRES_USER: sre_reflex
          POSTGRES_PASSWORD: sre_reflex
          POSTGRES_DB: sre_reflex_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U sre_reflex" --health-interval 2s --health-retries 20
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest
      - run: uv run pytest -m db
        env:
          TEST_DATABASE_URL: postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex_test
      - run: uv run sre-reflex eval --models fake --fixtures tests/fixtures/samples.jsonl

  model-server:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: server
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync
      - run: uv run pytest

  chart:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/setup-helm@v4
      - run: helm lint chart --set image.repository=example/sre-reflex
```

- [ ] **Step 4: Write the README**

`README.md`:
````markdown
# sre-reflex

Alert triage with **decision models** instead of giant LLM context windows.

When Prometheus fires an alert, sre-reflex gathers a small, pre-digested summary
(the alert, its 7-day history, a few metric trends, deduplicated error logs — under
800 tokens) and asks narrow questions:

| Question | Type |
|---|---|
| A human needs to take action on this alert. | yes/no probability |
| How severe is the impact? | 1–5 |
| This alert will resolve on its own. | yes/no probability |

Each alert is answered by two models side by side:

- **openjev** — [open-jev-deberta-v3-large](https://huggingface.co/com-kotobalabs/open-jev-deberta-v3-large),
  an Apache-2.0 decision model (a reproduction of the idea behind TypeSafe's Jev)
- **ollama** — a local LLM baseline returning the same answers as JSON

It runs in **shadow mode**: your existing alerting is untouched. sre-reflex posts its
scores to a separate ntfy topic with ✅ Real / 🔇 Noise buttons, and those taps become
the ground truth for evaluation.

## Architecture

```
Alertmanager ──webhook──▶ sre-reflex ──▶ collectors (Alertmanager payload, Prometheus, Loki)
                              │
                              ├──▶ DecisionModel adapters ──▶ open-jev server (GPU) / Ollama
                              ├──▶ Postgres (states, decisions, labels)
                              └──▶ ntfy (scores + signed label buttons)
```

## Results

Collecting shadow-mode data. The comparison report (`sre-reflex eval`) will be published
here after two weeks of labelled alerts.

## Quick start (local)

```bash
docker compose up -d --build postgres ntfy bot
curl -X POST localhost:8080/alertmanager -H 'content-type: application/json' \
  -d @tests/fixtures/webhook-example.json
```

## Development

```bash
uv sync
uv run pytest                      # unit tests
docker compose up -d postgres-test
TEST_DATABASE_URL=postgresql://sre_reflex:sre_reflex@localhost:5433/sre_reflex_test uv run pytest -m db
uv run sre-reflex eval --models fake --fixtures tests/fixtures/samples.jsonl
```

## Evaluation

```bash
sre-reflex eval --models openjev,ollama --labels hand --since 14d
sre-reflex eval --models openjev,ollama --replay        # re-run stored states
```

Reports precision/recall/F1 and Brier score for "actionable" against hand labels,
p50/p95 latency and cost per model, results with and without incomplete context, and
inter-model agreement for questions without ground truth.

## Deploying

- Bot: Helm chart in `chart/` (`image.repository`, `env`, `existingSecret`, `metricQueries`).
- Model server: `server/Dockerfile` on a GPU host (`OPENJEV_MODEL`, port 8000).
- Point an Alertmanager receiver at `http://<service>:8080/alertmanager`; expose only
  `/label` publicly.

## Licence

Apache-2.0
````

`tests/fixtures/webhook-example.json`:
```json
{
  "version": "4",
  "status": "firing",
  "receiver": "sre-reflex",
  "alerts": [
    {
      "status": "firing",
      "labels": {"alertname": "HighErrorRate", "severity": "warning", "namespace": "demo"},
      "annotations": {"summary": "demo 5xx rate above 5%"},
      "startsAt": "2026-09-23T11:56:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "demo0001"
    }
  ]
}
```

Also create `LICENSE` with the standard Apache-2.0 text: `curl -sL https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE`.

- [ ] **Step 5: Full verification**

Run: `uv run ruff check . && uv run pytest && (cd server && uv run pytest) && helm lint chart --set image.repository=example/sre-reflex`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add chart .github README.md LICENSE tests/fixtures/webhook-example.json
git commit -m "feat: Helm chart, CI workflow and README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Follow-up plan (not in this document)

the homelab GitOps repo deployment: verify RTX 3060 passthrough target; model-server + Ollama model on the GPU host; CNPG database and SealedSecret (`DATABASE_URL`, `LABEL_HMAC_KEY`, `NTFY_TOKEN`); ArgoCD Application for the chart; Alertmanager receiver (`continue: true` so existing routing is unchanged); tunnel route for `/label` only; `SreReflexDown` / `SreReflexModelServerUnreachable` rules; create the public GitHub repo and push.
