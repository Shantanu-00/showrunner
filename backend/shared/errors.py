"""One error shape for every service.

`{"code": "...", "message": "...", ...}` — machine-readable code first, because clients branch
on it: the outbox retries `RATE_LIMITED`, surfaces `EVENT_NOT_LIVE` as a banner, and the host
console renders `CAPACITY` (spec 11 §1.2) as the contact-the-developer flow.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class ApiError(HTTPException):
    def __init__(self, http_status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(http_status, detail={"code": code, "message": message, **extra})
        self.code = code


def bad_request(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, code, message, **extra)


def forbidden(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, code, message, **extra)


def not_found(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, code, message, **extra)


def conflict(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, code, message, **extra)


def rate_limited(message: str, **extra: Any) -> ApiError:
    return ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "RATE_LIMITED", message, **extra)
