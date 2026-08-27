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


#: Fields promoted into the one-line summary of a stage log, in this order. Everything else still
#: gets emitted as a queryable label, it just does not earn space in the collapsed row.
_STAGE_SUMMARY_FIELDS = (
    "stage",
    "media_id",
    "ms",
    "tokens_in",
    "tokens_out",
    "stage_id",
    "aesthetic",
    "highlight",
    "verdict",
    "faces",
    "visibility",
    "err",
)


def stage(outcome: str, **fields: Any) -> None:
    """Log one pipeline stage as a single readable line *and* as structured labels.

    Logs Explorer shows a JSON payload's `message` as the collapsed summary and everything else
    only after expanding the entry. Since these entries are read off a screen — the pipeline's
    latency and token cost are meant to be legible at a glance, not after a click — the summary
    carries the numbers inline (`stage=curate media=01J… ms=1180 tokens_in=1548 verdict=highlight`)
    while the same values stay individually queryable for the Flight Deck and the cost ticker.

    Keep the field names stable: `docs/specs/10-pipeline-visualizer.md` reads them back.
    """
    parts = [f"{key}={fields[key]}" for key in _STAGE_SUMMARY_FIELDS if fields.get(key) is not None]
    severity = "ERROR" if outcome in ("failed", "failed_permanent") else "INFO"
    _emit(severity, " ".join([f"{outcome}:", *parts]), **fields)


def debug(message: str, **fields: Any) -> None:
    _emit("DEBUG", message, **fields)


def info(message: str, **fields: Any) -> None:
    _emit("INFO", message, **fields)


def warn(message: str, **fields: Any) -> None:
    _emit("WARNING", message, **fields)


def error(message: str, **fields: Any) -> None:
    _emit("ERROR", message, **fields)
