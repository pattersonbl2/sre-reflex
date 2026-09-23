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
