"""Helpers for logging exceptions without leaking URLs or secrets."""
import httpx


def describe_exc(exc: BaseException) -> str:
    """Return the exception type (plus HTTP status code if any), never its message.

    httpx errors embed the full request URL in their string form, and the ntfy and
    label URLs carry credentials/signatures, so messages are deliberately dropped.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__} status={exc.response.status_code}"
    return type(exc).__name__
