"""Error taxonomy shared by every platform component."""

from __future__ import annotations


class LabError(Exception):
    """Base error. ``status`` maps to the HTTP status used at the API edge."""

    status = 400
    code = "lab_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(LabError):
    status = 404
    code = "not_found"


class Conflict(LabError):
    status = 409
    code = "conflict"


class ValidationFailed(LabError):
    status = 422
    code = "validation_failed"


class DeviceError(LabError):
    """Permanent southbound failure (commit rejected, verification failed)."""

    status = 502
    code = "device_error"


class TransientError(LabError):
    """Retryable southbound failure."""

    status = 503
    code = "transient_error"
