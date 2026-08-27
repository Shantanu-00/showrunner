"""Structured JSON logging.

Cloud Run parses single-line JSON on stdout: `severity` colours the log, `message` is the
summary, and every other key becomes a queryable label. Worker stage timings and latencies
logged here are what the Flight Deck (spec 10) and the cost ticker read back later, so the
field names are part of the contract: `event_id`, `media_id`, `stage`, `ms`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_SERVICE = "unknown"


def configure(service: str) -> None:
    global _SERVICE
    _SERVICE = service


def _emit(severity: str, message: str, **fields: Any) -> None:
    record: dict[str, Any] = {"severity": severity, "message": message, "service": _SERVICE}
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    try:
        line = json.dumps(record, default=str)
    except (TypeError, ValueError):
        line = json.dumps({"severity": "ERROR", "message": f"unserialisable log: {message}"})
    stream = sys.stderr if severity in ("ERROR", "CRITICAL") else sys.stdout
    print(line, file=stream, flush=True)


def debug(message: str, **fields: Any) -> None:
    _emit("DEBUG", message, **fields)


def info(message: str, **fields: Any) -> None:
    _emit("INFO", message, **fields)


def warn(message: str, **fields: Any) -> None:
    _emit("WARNING", message, **fields)


def error(message: str, **fields: Any) -> None:
    _emit("ERROR", message, **fields)
