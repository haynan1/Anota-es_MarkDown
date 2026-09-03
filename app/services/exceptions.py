"""Domain errors raised by services and translated to HTTP by the blueprints."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(ServiceError):
    status_code = 400


class ConflictError(ServiceError):
    """The document changed on the server since the client last read it."""

    status_code = 409

    def __init__(self, message: str, server_state: dict | None = None) -> None:
        super().__init__(message)
        self.server_state = server_state or {}


class NotFoundError(ServiceError):
    status_code = 404


class LockedError(ServiceError):
    """The target is protected and refuses to change.

    ``423 Locked`` rather than ``403``: the request was allowed, the caller is
    who they claim to be, and nothing about the payload was wrong - the
    resource itself is closed. The canvas reads the status to tell "you cannot
    do this" apart from "you did this wrong", and only the first one is worth
    offering the key for.
    """

    status_code = 423
