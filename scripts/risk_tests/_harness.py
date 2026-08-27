"""Shared plumbing for the B1 risk probes.

Each probe is a standalone script that answers exactly one GO/NO-GO question about a
platform capability we have already designed around. Probes are cheap, idempotent and
re-runnable; they write verdicts to `artifacts/results.json` plus a human-readable
`artifacts/RESULTS.md` that gets pasted into HANDOFF §9.

No probe asserts anything. A probe reports what the platform actually did — including
the exact error text when it refuses, because that error text is the finding.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"

# The Windows dev box's console is cp1252: an arrow or an em dash in a probe note would
# otherwise raise UnicodeEncodeError and mask the actual finding.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # non-reconfigurable stream (pytest capture, pipes)
        pass

GO = "GO"
NO_GO = "NO-GO"
PARTIAL = "PARTIAL"
ERROR = "ERROR"


# ---------------------------------------------------------------- env


def load_env() -> None:
    """Load `.env` from the repo root into os.environ (existing vars win)."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split("#", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def env(key: str, default: str | None = None) -> str:
    load_env()
    value = os.environ.get(key, default)
    if value is None:
        raise SystemExit(f"missing required env var: {key} (add it to .env)")
    return value


def project() -> str:
    return env("GOOGLE_CLOUD_PROJECT")


def location() -> str:
    return env("GOOGLE_CLOUD_LOCATION", "us-central1")


# ---------------------------------------------------------------- genai clients


def enterprise_client(loc: str | None = None):
    """Client on the billed first-party Vertex/GEAP path — the only path guest media may use.

    `enterprise=True` is the 2026 replacement for `vertexai=True`
    (env flag: GOOGLE_GENAI_USE_ENTERPRISE).
    """
    from google import genai

    return genai.Client(enterprise=True, project=project(), location=loc or location())


def apikey_client():
    """Client on the AI Studio path. Dev-only: free tier trains on submitted data,
    so this path never sees guest media (EXECUTION-PLAN §5, README privacy line)."""
    from google import genai

    key = env("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set — AI Studio fallback unavailable")
    return genai.Client(api_key=key)


def access_token() -> str:
    """ADC bearer token for REST calls to APIs without a pinned client library."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ---------------------------------------------------------------- verdicts


@dataclass
class Verdict:
    probe: str
    question: str
    verdict: str = ERROR
    headline: str = ""
    seconds: float = 0.0
    cost_usd: float = 0.0
    evidence: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    gate: str = ""

    def note(self, line: str) -> None:
        self.findings.append(line)
        print(f"  · {line}", flush=True)


def artifact(name: str) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS / name


def save_bytes(name: str, data: bytes) -> str:
    path = artifact(name)
    path.write_bytes(data)
    rel = path.relative_to(ROOT).as_posix()
    print(f"  · wrote {rel} ({len(data):,} bytes)", flush=True)
    return rel


def run(probe: str, question: str, body: Callable[[Verdict], None], gate: str = "") -> Verdict:
    """Run a probe body, time it, persist the verdict. Never raises."""
    v = Verdict(probe=probe, question=question, gate=gate)
    print(f"\n=== probe: {probe} ===\n{question}", flush=True)
    started = time.monotonic()
    try:
        body(v)
    except Exception as exc:  # a probe failing IS a result — capture, don't crash
        v.verdict = ERROR
        v.headline = f"{type(exc).__name__}: {exc}"
        v.note("traceback: " + traceback.format_exc(limit=3).replace("\n", " | "))
    v.seconds = round(time.monotonic() - started, 1)
    print(f"--- {probe}: {v.verdict} ({v.seconds}s) — {v.headline}", flush=True)
    persist(v)
    return v


def persist(v: Verdict) -> None:
    path = artifact("results.json")
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data[v.probe] = asdict(v)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    write_report(data)


ORDER = ["lyria", "veo", "banana", "armor", "face_run", "photos"]


def write_report(data: dict[str, Any]) -> None:
    rows, sections = [], []
    total_cost = 0.0
    for name in ORDER + [k for k in data if k not in ORDER]:
        r = data.get(name)
        if not r:
            continue
        total_cost += r.get("cost_usd", 0.0)
        rows.append(
            f"| `{name}` | **{r['verdict']}** | {r['headline']} | {r['seconds']}s | "
            f"${r.get('cost_usd', 0):.3f} |"
        )
        lines = [f"### `{name}` — {r['verdict']}", "", f"**Question:** {r['question']}", ""]
        if r.get("gate"):
            lines += [f"**Gate:** {r['gate']}", ""]
        lines += [f"**Result:** {r['headline']}", ""]
        for f in r.get("findings", []):
            lines.append(f"- {f}")
        for e in r.get("evidence", []):
            lines.append(f"- evidence: `{e}`")
        sections.append("\n".join(lines))

    body = "\n".join(
        [
            "# B1-S1 Risk Probe Results",
            "",
            "Generated by `scripts/risk_tests/run_all.py`. Local artifact — the durable",
            "record is HANDOFF §9 + `docs/context/friction-log.md`.",
            "",
            "| Probe | Verdict | Headline | Time | Cost |",
            "|---|---|---|---|---|",
            *rows,
            "",
            f"**Measured spend across probes: ${total_cost:.2f}**",
            "",
            "---",
            "",
            "\n\n".join(sections),
            "",
        ]
    )
    artifact("RESULTS.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------- shared test asset

PORTRAIT = "cast_portrait.png"


def test_portrait() -> Path:
    """A photoreal portrait of a synthetic person, used by the Veo and face probes.

    Deliberately AI-generated: no real guest's biometric data goes through a probe
    (HANDOFF §8 demo-dataset rule). Generated once by `banana.py`, cached on disk.
    Pass `--image <path>` to any probe to substitute your own photo instead.
    """
    path = artifact(PORTRAIT)
    if path.exists():
        return path
    raise SystemExit(
        f"missing {path.relative_to(ROOT).as_posix()} — run `python scripts/risk_tests/banana.py` "
        "first (it generates the synthetic cast portrait), or pass --image <your-photo>"
    )


def image_arg() -> Path | None:
    """`--image <path>` override, for probing with a real photo instead of the cast portrait."""
    if "--image" in sys.argv:
        idx = sys.argv.index("--image")
        if idx + 1 < len(sys.argv):
            return Path(sys.argv[idx + 1]).resolve()
    return None
