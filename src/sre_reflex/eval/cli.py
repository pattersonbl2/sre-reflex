import argparse

from sre_reflex.config import Settings


def add_eval_args(parser: argparse.ArgumentParser) -> None:
    pass


async def run_eval(args: argparse.Namespace, settings: Settings) -> str:
    raise NotImplementedError("implemented in Task 16")
