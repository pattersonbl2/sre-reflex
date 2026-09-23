# sre-reflex v1 — Design

**Date:** 2026-09-23
**Status:** Approved in brainstorming, pending spec review

## Goal

An alert-triage bot that answers narrow questions about each Prometheus alert using a
*decision model* (Jev-style: text state + typed questions → probabilities) instead of
feeding large context windows to an LLM.

It is built in two stages:

1. **Homelab tool** — triage the author's own alerts.
2. **Portfolio project** — public GitHub repo whose headline artifact is a measured
   comparison of a decision model vs an LLM baseline on real alerts.

v1 runs in **shadow mode**: it never suppresses, reroutes, or modifies existing alerts.

## Non-goals (v1)

- Suppressing or re-prioritising alerts (v2: ntfy priority routing once evals justify it)
- Deploy correlation (ArgoCD sync / image update checks)
- Grouping duplicate alerts into incidents
- Auto-remediation
- The TypeSafe Jev API adapter (added when early access is granted; the interface is designed for it)
- Multi-tenant or non-homelab deployments

## Architecture

```
Alertmanager ──webhook──▶ sre-reflex bot (k8s, ArgoCD)
   │ (existing routing          │
   │  unchanged)                ├─ 1. collect context ──▶ Alertmanager API, Prometheus, Loki
   ▼                            ├─ 2. build state (≤ 800 tokens)
 ntfy (normal alert)            ├─ 3. ask questions ──▶ DecisionModel interface
                                │        ├─ openjev adapter ──▶ model server (the GPU host, RTX 3060)
                                │        ├─ ollama adapter  ──▶ Ollama (the GPU host) — LLM baseline
                                │        └─ jev adapter     (future)
                                ├─ 4. persist state + all answers ──▶ Postgres (CloudNativePG)
                                └─ 5. post scores + ✅/🔇 buttons ──▶ ntfy topic "sre-reflex"

 Phone taps ✅/🔇 ──▶ tunnel ──▶ bot /label (signed link) ──▶ Postgres
 Hourly job: unlabelled resolved alerts ──▶ inferred labels
 `sre-reflex eval` CLI: replay stored states through adapters ──▶ markdown report
```

### Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Deployment | Bot in k8s; inference on the GPU host | Stateless app tier + separate GPU inference tier; the adapter interface is the network boundary |
| Output | Separate ntfy message on topic `sre-reflex` | Alertmanager untouched; bot failure loses nothing |
| Models per alert | Both openjev and ollama, every alert | Comparison data from day one |
| Context | All four collectors, pre-digested into short text | Small, relevant state; numbers computed in code because open-jev is weak at arithmetic |
| Labels | ntfy buttons (hand) + hourly inference (fallback) | Low-friction ground truth; inferred labels kept separate |
| Repo | Public GitHub `sre-reflex`; deployment wiring in the homelab GitOps repo | Public repo contains no homelab IPs, hostnames, or secrets |

## Components

Bot: Python 3.12, FastAPI, httpx, psycopg 3, Pydantic settings. Model server: separate
FastAPI app and container image.

### Repository layout

```
sre-reflex/
  src/sre_reflex/
    app.py              # FastAPI app: /alertmanager, /label, /healthz, /metrics
    config.py           # Pydantic settings from env
    collectors/
      base.py           # Collector protocol + timeout wrapper
      alert.py
      history.py
      metrics.py
      logs.py
    state.py            # State builder + token cap
    questions.py        # Versioned question set
    models/
      base.py           # DecisionModel protocol, Question/Answer types
      openjev.py
      ollama.py
      fake.py           # Deterministic adapter for tests/CI
    store.py
    notify.py
    labels.py           # /label handler + inference job
    eval/
      cli.py            # `sre-reflex eval`
      report.py
  server/               # open-jev model server (own Dockerfile)
  chart/                # Helm chart for the bot
    migrations/         # SQL migrations (package data)
  tests/
    fixtures/           # Recorded API responses + ~20 scrubbed sample alerts
  docker-compose.yml    # Postgres + fake model server + ntfy mock for e2e
  .env.example
  README.md
```

### Collectors

Each collector implements `async collect(alert) -> CollectorResult(name, lines: list[str], ok: bool)`
with a per-collector timeout (default 3 s). Failure returns `ok=False` and no lines; it
never raises out of the pipeline.

| Collector | Source | Output (example) |
|---|---|---|
| `alert` | Webhook payload | `Alert HighErrorRate (severity=warning) on service=n8n namespace=n8n, firing 4m.` / summary + description annotations |
| `history` | Prometheus `ALERTS{alertstate="firing"}` over 7 d, split into episodes | `Fired 14 times in 7 days; resolved 13 times, median duration 3m.` |
| `metrics` | PromQL from a per-alertname query map (config file); fallback: the alert's own expr | `Error rate rose from 0.2% to 8.1%, sustained 6m.` |
| `logs` | Loki `query_range`, error-level lines for the alert's namespace/pod, last 15 m | ≤ 25 deduplicated lines, each truncated to 200 chars, scrubbed |

Log scrubbing replaces token-like strings (≥ 20 chars base64/hex), emails, and IPv4
addresses with placeholders before anything is stored or sent to a model.

### State builder (`state.py`)

Concatenates collector sections in fixed order (alert, history, metrics, logs) under
headers. Enforces a cap of 800 tokens, estimated as `ceil(len(text) / 4)` (no tokenizer download needed),
trimming log lines first, then metrics lines. Records which collectors succeeded and the
final token count.

### Questions (`questions.py`)

Question set version `q_version = 1`:

| id | Type | Text | Options / range |
|---|---|---|---|
| `actionable` | yes/no | "A human needs to take action on this alert." | probability 0–1 |
| `severity` | score | "How severe is the impact of this alert?" | 1 (cosmetic) … 5 (outage) |
| `self_resolving` | yes/no | "This alert will resolve on its own without intervention." | probability 0–1 |

Changing wording or options requires bumping `q_version`.

### DecisionModel interface (`models/base.py`)

```python
class DecisionModel(Protocol):
    name: str
    async def decide(self, state: str, questions: list[Question]) -> list[Answer]: ...

class Answer(BaseModel):
    question_id: str
    value: float | int | str       # probability, score level, or chosen option
    distribution: dict[str, float]  # full probability distribution over options
    latency_ms: int
    cost_usd: float                 # 0.0 for local models
```

Adapters:

- **openjev** — `POST {MODEL_SERVER_URL}/decide`, timeout 5 s.
- **ollama** — Ollama `/api/chat` with a JSON-schema `format` returning a probability per
  option, model configurable (default `qwen3:8b`), temperature 0, timeout 30 s.
- **fake** — deterministic hash-based answers for tests and CI.

All adapters must pass a shared contract test suite (`tests/test_model_contract.py`):
one answer per question, distributions sum to 1 ± 0.01, values within range.

### Model server (`server/`)

FastAPI app wrapping `typed_decisions.open_jev.OpenJev` loaded from
`com-kotobalabs/open-jev-deberta-v3-large` on CUDA (CPU fallback). Endpoints:
`POST /decide`, `GET /healthz`, `GET /metrics`. Runs as a container on the GPU host via the
existing the homelab GitOps repo stacks pattern. **Before deploying, verify the RTX 3060
passthrough target** (the homelab docs flag it as unconfirmed).

### Notify (`notify.py`)

Posts to ntfy topic `sre-reflex` (priority default, no suppression). Example:

```
HighErrorRate · n8n
actionable 0.22 · severity 2/5 · self-resolving 0.87   (openjev)
ollama: actionable 0.35 · severity 2/5 · self-resolving 0.70
missing: logs
[✅ Real] [🔇 Noise]
```

Buttons are ntfy `http` actions to
`{PUBLIC_LABEL_URL}/label?a={alert_id}&v={real|noise}&exp={unix}&sig={hmac}`.
If Postgres is unavailable, the message is sent without buttons.

### Labels (`labels.py`)

- **Hand labels** — `/label` verifies HMAC-SHA256 over `alert_id|value|exp` with
  `LABEL_HMAC_KEY`, rejects expired (> 7 d) or already-used signatures (the signature is
  stored on first use), and upserts `labels(source='hand')`. Latest hand label wins.
- **Inferred labels** — hourly CronJob running `sre-reflex infer-labels`: an alert that resolved within 10 minutes of firing
  and has no hand label 24 h after resolution gets `labels(value='noise', source='inferred')`.
  No other inference rules in v1; ambiguous alerts stay unlabelled.

### Eval CLI (`eval/`)

`sre-reflex eval --models openjev,ollama [--labels hand|all] [--since 14d] [--replay] [--fixtures PATH] [--out PATH]`

- Default: evaluate stored decisions. `--replay`: re-run stored states through adapters.
  `--fixtures`: replay a committed JSONL file instead of the database (used by CI).
- Ground truth for `actionable`: label `real` = positive. `severity` and
  `self_resolving` have no ground truth in v1; the report shows their distributions
  and inter-model agreement only.
- Reports per model: precision, recall, F1 at threshold 0.5; Brier score (calibration);
  p50/p95 latency; total cost; count of states with missing collectors (and results
  with those excluded).
- Output: markdown to stdout or `--out report.md`.

## Data model (Postgres, CloudNativePG cluster in homelab)

```sql
alerts(id bigserial pk, fingerprint text, alertname text, labels jsonb,
       fired_at timestamptz, resolved_at timestamptz,
       unique (fingerprint, fired_at))
states(id bigserial pk, alert_id fk, text text, collectors_ok jsonb,
       token_count int, created_at timestamptz)
decisions(id bigserial pk, state_id fk, model text, question_id text, q_version int,
          value text, distribution jsonb, latency_ms int, cost_usd numeric,
          created_at timestamptz)
labels(id bigserial pk, alert_id fk, value text check (value in ('real','noise')),
       source text check (source in ('hand','inferred')), sig text unique null,
       created_at timestamptz)
```

Alertmanager `resolved` webhooks update `alerts.resolved_at`.

## Error handling

- Webhook returns 200 immediately; processing happens in a background task. Duplicate
  deliveries are deduped on `(fingerprint, fired_at)`.
- Adapter failure or timeout: log it and increment `sre_reflex_adapter_errors_total`, continue with the other adapter; ntfy
  shows `openjev: unavailable`.
- Collector failure: partial state, recorded in `collectors_ok`.
- Postgres down: ntfy sent without buttons; error logged and counted.
- Alerts named `SreReflexDown` and `SreReflexModelServerUnreachable` are skipped by
  the bot (never self-triaged).

## Observability

Prometheus metrics on `/metrics`: `sre_reflex_alerts_processed_total`,
`sre_reflex_adapter_latency_seconds{model}`, `sre_reflex_adapter_errors_total{model}`,
`sre_reflex_collector_errors_total{collector}`, `sre_reflex_labels_total{source}`.
ServiceMonitor in the Helm chart. Alert rules `SreReflexDown` and
`SreReflexModelServerUnreachable` added in the homelab GitOps repo.

## Security

- Only `/label` is exposed via tunnel (Pangolin or Cloudflare, chosen at deploy time);
  `/alertmanager` is cluster-internal.
- Secrets (`LABEL_HMAC_KEY`, Postgres credentials, ntfy token) live as SealedSecrets in
  the homelab GitOps repo only. The public repo ships `.env.example`.
- Stored state text is scrubbed (see Collectors), so fixtures can be published.

## Configuration (env)

`PROMETHEUS_URL`, `LOKI_URL`, `MODEL_SERVER_URL`, `OLLAMA_URL`,
`OLLAMA_MODEL`, `DATABASE_URL`, `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`,
`PUBLIC_LABEL_URL`, `LABEL_HMAC_KEY`, `METRIC_QUERIES_FILE`, `ENABLED_MODELS` (default `openjev,ollama`; first is primary in ntfy).

## Testing

- **Unit:** collectors vs recorded fixtures; state builder token cap and trim order;
  HMAC valid/expired/tampered/replayed; inference rule; log scrubbing.
- **Contract:** shared suite run against every adapter (fake always; openjev/ollama
  behind an opt-in marker).
- **End-to-end:** docker-compose (Postgres, fake model server, ntfy mock) — post a
  sample Alertmanager webhook, assert rows in `decisions` and the outgoing ntfy request.
- **CI eval smoke:** `sre-reflex eval --models fake` over ~20 committed scrubbed fixtures.

## Deployment split

| Repo | Contents |
|---|---|
| `sre-reflex` (public GitHub) | Code, model server, Helm chart, migrations, tests, README |
| homelab GitOps repo | ArgoCD Application, values file, SealedSecrets, CNPG database, model-server stack on the GPU host, tunnel route, alert rules |

## Definition of done (v1)

1. Every real homelab alert is scored by openjev and ollama in shadow mode.
2. ✅/🔇 buttons record hand labels from the phone.
3. After two weeks of labelling, `sre-reflex eval` produces the comparison report.
4. The report is published in the README.
