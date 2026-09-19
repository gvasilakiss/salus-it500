"""Shared helpers used by both Salus transports.

Kept transport-agnostic and dependency-light so it can be unit tested without
Home Assistant installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiohttp import ClientError

from .exceptions import SalusConnectionError

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Bounded, iterative backoff. Never recursive, never unbounded - Salus has a
#: history of temporarily blocking IP addresses that hammer it after errors.
RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

#: Query/body parameter names that must never reach a log line or exception
#: message. aiohttp exceptions sometimes stringify the full request URL
#: (including query parameters), so anything derived from a caught network
#: error is passed through this before it is logged or re-raised.
_SECRET_RE = re.compile(r"(secToken|token|password|pass|pwd)=[^&\s'\"]+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Strip token/password-shaped query values out of a string.

    Applied to anything derived from ``str(exception)`` before it is logged
    or embedded in a Home Assistant-facing error, since aiohttp connection
    errors can include the full request URL.
    """
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


async def async_request_with_retry(
    call: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int = 4,
    what: str = "Salus",
) -> _T:
    """Run ``call``, retrying network-level failures with bounded backoff.

    Only transport-level failures (timeouts, dropped connections, DNS
    errors) are retried here - a bounded, iterative loop, never recursive.
    HTTP status codes such as 429 are the caller's responsibility to handle;
    they must propagate immediately rather than being retried locally, so a
    single polling cycle never turns into a burst of requests against a
    service that just asked us to slow down.
    """
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await call()
        except (ClientError, asyncio.TimeoutError) as err:
            last_err = err
            if attempt == max_attempts - 1:
                break
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            _LOGGER.debug(
                "%s network error (%s); retrying in %.0fs (attempt %d/%d)",
                what,
                redact_secrets(str(err)),
                delay,
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(delay)

    raise SalusConnectionError(
        f"Could not reach {what}: {redact_secrets(str(last_err))}"
    ) from last_err
