"""
Explicit, actionable application errors.

Every error carries a stable machine-readable `code` (see section 19 of the
build spec) plus a human-actionable `message` and optional `details`. Never
raise a bare Exception from a module that a caller needs to branch on.
"""
from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    code: str = "UNKNOWN_ERROR"
    http_status: int = 400

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "details": self.details}


class InvalidJsonError(AppError):
    code = "INVALID_JSON"


class InvalidSchemaError(AppError):
    code = "INVALID_SCHEMA"


class UnsupportedSchemaVersionError(AppError):
    code = "UNSUPPORTED_SCHEMA_VERSION"


class MissingRequiredFieldError(AppError):
    code = "MISSING_REQUIRED_FIELD"


class DuplicateClipIdError(AppError):
    code = "DUPLICATE_CLIP_ID"


class InvalidTimestampError(AppError):
    code = "INVALID_TIMESTAMP"


class TimestampOutOfRangeError(AppError):
    code = "TIMESTAMP_OUT_OF_RANGE"


class InvalidDurationError(AppError):
    code = "INVALID_DURATION"


class VideoDownloadFailedError(AppError):
    code = "VIDEO_DOWNLOAD_FAILED"


class VideoNotFoundError(AppError):
    code = "VIDEO_NOT_FOUND"
    http_status = 404


class VideoCorruptedError(AppError):
    code = "VIDEO_CORRUPTED"


class UnsupportedVideoFormatError(AppError):
    code = "UNSUPPORTED_VIDEO_FORMAT"


class FfprobeFailedError(AppError):
    code = "FFPROBE_FAILED"


class FfmpegFailedError(AppError):
    code = "FFMPEG_FAILED"


class NoVideoStreamError(AppError):
    code = "NO_VIDEO_STREAM"


class StorageError(AppError):
    code = "STORAGE_ERROR"


class OutputValidationFailedError(AppError):
    code = "OUTPUT_VALIDATION_FAILED"


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404


class AuthorizationRequiredError(AppError):
    code = "AUTHORIZATION_REQUIRED"
