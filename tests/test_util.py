"""Tests for shared transport helpers (custom_components.salus_it500.api.util)."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientConnectionError

from custom_components.salus_it500.api.exceptions import SalusConnectionError
from custom_components.salus_it500.api.util import (
    async_request_with_retry,
    redact_secrets,
)


def test_redact_secrets_masks_sectoken():
    text = "GET /x?devId=1&secToken=abcdef123456 failed"
    redacted = redact_secrets(text)
    assert "abcdef123456" not in redacted
    assert "secToken=***" in redacted


def test_redact_secrets_masks_password_and_token_case_insensitively():
    text = "TOKEN=abc123&Password=hunter2"
    redacted = redact_secrets(text)
    assert "abc123" not in redacted
    assert "hunter2" not in redacted


def test_redact_secrets_leaves_unrelated_text_alone():
    text = "Cannot connect to host sal-emea-p01-api.arrayent.com:443"
    assert redact_secrets(text) == text


async def test_async_request_with_retry_succeeds_first_try():
    calls = []

    async def call():
        calls.append(1)
        return "ok"

    result = await async_request_with_retry(call)
    assert result == "ok"
    assert len(calls) == 1


async def test_async_request_with_retry_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    attempts = []

    async def call():
        attempts.append(1)
        if len(attempts) < 3:
            raise ClientConnectionError("boom")
        return "recovered"

    result = await async_request_with_retry(call, max_attempts=4)
    assert result == "recovered"
    assert len(attempts) == 3


async def test_async_request_with_retry_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    attempts = []

    async def call():
        attempts.append(1)
        raise ClientConnectionError("secToken=leaked-value always fails")

    with pytest.raises(SalusConnectionError) as excinfo:
        await async_request_with_retry(call, max_attempts=3)

    assert len(attempts) == 3
    assert "leaked-value" not in str(excinfo.value)


async def test_async_request_with_retry_does_not_retry_other_exceptions():
    attempts = []

    async def call():
        attempts.append(1)
        raise ValueError("not a network error")

    with pytest.raises(ValueError):
        await async_request_with_retry(call, max_attempts=4)
    assert len(attempts) == 1


async def _fast_sleep(_seconds: float) -> None:
    """Stand-in for asyncio.sleep so retry tests don't take real seconds."""
    return None
