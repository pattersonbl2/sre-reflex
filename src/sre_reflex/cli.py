import argparse
import asyncio
from datetime import UTC, datetime

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
        return await infer_labels(store, datetime.now(UTC))
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
