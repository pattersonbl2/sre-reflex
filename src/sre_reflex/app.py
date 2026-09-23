from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from sre_reflex.alerts import AlertmanagerWebhook
from sre_reflex.collectors.alert import AlertCollector
from sre_reflex.collectors.history import HistoryCollector
from sre_reflex.collectors.logs import LogsCollector
from sre_reflex.collectors.metrics import MetricsCollector, load_metric_queries
from sre_reflex.config import Settings, check_startup_security
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
    if pipeline is None:
        check_startup_security(settings)

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
            datetime.now(UTC),
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
