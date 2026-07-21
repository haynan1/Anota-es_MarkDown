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
