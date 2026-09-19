"""Exceptions raised by the Salus iT500 clients."""

from __future__ import annotations


class SalusError(Exception):
    """Base class for all Salus errors."""


class SalusAuthError(SalusError):
    """Credentials were rejected, or the session could not be established."""


class SalusConnectionError(SalusError):
    """The Salus cloud could not be reached."""


class SalusRateLimitError(SalusConnectionError):
    """Salus is throttling or has temporarily blocked this IP address."""


class SalusDeviceNotFound(SalusError):
    """The configured device ID is not present on this account."""


class SalusProtocolError(SalusError):
    """A response came back but wasn't shaped the way the protocol expects.

    Covers malformed XML/JSON and unexpected payloads - a wire-format
    surprise, as distinct from the request being understood and rejected
    (:class:`SalusDeviceError`) or the transport not working at all
    (:class:`SalusConnectionError`).
    """


class SalusDeviceError(SalusError):
    """A command was understood but the device or cloud rejected it."""


class SalusValidationError(SalusError):
    """Data failed validation before it was ever sent to Salus.

    Raised locally - schedule entries, temperatures, and similar user or
    service-call input - never as a result of a network round trip.
    """


class SalusUnsupportedFeature(SalusError):
    """The active transport cannot perform this operation."""

