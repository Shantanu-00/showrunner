"""Probe: is the Google Photos "send to my camera roll" export path still buildable?

Spec 02 §6 tiers this deliberately, because the Photos API surface was restricted in 2025:
  * P0 — Web Share / zip download (no API, always works)
  * P1 — "Send to Google Photos": OAuth `photoslibrary.appendonly` → upload bytes →
         `mediaItems:batchCreate` into an event album → deep link

The P1 tier is a judge-pleasing Google-ecosystem moment, but only if the scope and the
batchCreate method still exist for app-created content. Spec 02 budgets a 30-minute
Day-1 check; this is that check, automated.

What this probe CAN establish with zero user interaction and zero spend:
  1. Is the Photos Library API still offered to this project at all?
  2. Does the live discovery document still publish `mediaItems.batchCreate`, the
     `uploads` endpoint, and the `appendonly` scope — i.e. is the documented flow intact?
  3. Which scopes does each required method now demand (this is what changed in 2025)?

What it deliberately does NOT do: complete an OAuth consent flow. That needs a consent
screen, a client ID and a human clicking Allow — manual work that only makes sense once
the surface above checks out. The probe prints the exact remaining manual steps.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

import _harness as H

DISCOVERY = "https://photoslibrary.googleapis.com/$discovery/rest?version=v1"
SERVICE = "photoslibrary.googleapis.com"

APPENDONLY = "https://www.googleapis.com/auth/photoslibrary.appendonly"
REQUIRED_METHODS = ("mediaItems.batchCreate", "albums.create")


def _walk_methods(node: dict, prefix: str = "") -> dict[str, dict]:
    """Flatten a discovery doc's resource tree into {dotted.method: schema}."""
    found: dict[str, dict] = {}
    for name, method in (node.get("methods") or {}).items():
        found[f"{prefix}{name}"] = method
    for name, child in (node.get("resources") or {}).items():
        found.update(_walk_methods(child, f"{prefix}{name}."))
    return found


def _api_offered(v: H.Verdict) -> str:
    """Is the API listable for this project? (offered / not-offered / unknown)"""
    # On Windows gcloud is a .cmd shim, so bare "gcloud" is not an executable file.
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        v.note("gcloud not on PATH — skipping the service-availability check")
        return "unknown"
    try:
        result = subprocess.run(
            [
                gcloud, "services", "list", "--available",
                f"--filter=config.name={SERVICE}",
                "--format=value(config.name)",
                "--project", H.project(),
            ],
            capture_output=True, text=True, timeout=180, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        v.note(f"could not query service availability: {exc}")
        return "unknown"
    if result.returncode != 0:
        v.note(f"gcloud services list failed: {result.stderr.strip()[:200]}")
        return "unknown"
    return "offered" if SERVICE in result.stdout else "not-offered"


def body(v: H.Verdict) -> None:
    offered = _api_offered(v)
    v.note(f"{SERVICE} availability to this project: {offered}")

    response = requests.get(DISCOVERY, timeout=60)
    if response.status_code != 200:
        v.verdict = H.NO_GO
        v.headline = (
            f"discovery document unreachable ({response.status_code}) — treat the Photos "
            "export as gone; ship the zip/Web Share tier only."
        )
        return

    doc = response.json()
    methods = _walk_methods(doc)
    v.evidence.append(
        H.save_bytes(
            "photos_discovery_summary.json",
            json.dumps(
                {
                    "revision": doc.get("revision"),
                    "version": doc.get("version"),
                    "declared_scopes": sorted((doc.get("auth", {}).get("oauth2", {}).get("scopes") or {})),
                    "methods": {name: m.get("scopes", []) for name, m in sorted(methods.items())},
                },
                indent=2,
            ).encode(),
        )
    )

    declared_scopes = set((doc.get("auth", {}).get("oauth2", {}).get("scopes") or {}))
    v.note(f"discovery revision {doc.get('revision')}; {len(methods)} methods, {len(declared_scopes)} scopes")

    missing = [m for m in REQUIRED_METHODS if m not in methods]
    appendonly_declared = APPENDONLY in declared_scopes

    for name in REQUIRED_METHODS:
        if name in methods:
            v.note(f"{name}: present, scopes={methods[name].get('scopes')}")
        else:
            v.note(f"{name}: MISSING from the discovery document")
    v.note(f"appendonly scope declared: {appendonly_declared}")
    v.note(
        "note: the raw byte-upload endpoint (POST /v1/uploads) is never listed in a "
        "discovery document — it is a plain HTTP endpoint documented separately, so its "
        "absence here is expected and not evidence either way"
    )

    if missing or not appendonly_declared:
        v.verdict = H.NO_GO
        v.headline = (
            f"documented export flow is broken (missing: {missing or 'none'}, "
            f"appendonly declared: {appendonly_declared}) — ship the P0 zip / Web Share "
            "tier and drop the Photos button. No schedule impact; it was already tiered."
        )
        return

    v.verdict = H.PARTIAL
    v.headline = (
        f"API surface intact (discovery rev {doc.get('revision')}): batchCreate, albums.create "
        f"and the appendonly scope are all still published, API is {offered} to this project. "
        "Cannot be confirmed end-to-end without an OAuth consent screen + a human click, "
        "so it stays P1 behind the zip tier."
    )
    v.note("Remaining manual steps if we choose to build it (~30 min, spec 02 §6 P1):")
    v.note("  1. OAuth consent screen: External, scope photoslibrary.appendonly (sensitive → unverified-app cap of 100 test users is fine for a demo)")
    v.note("  2. Create a Web OAuth client ID; add the hosted origin + redirect URI")
    v.note(f"  3. gcloud services enable {SERVICE} --project {H.project()}")
    v.note("  4. Incremental consent from /gallery, then Cloud Run Job streams GCS originals -> /v1/uploads -> mediaItems:batchCreate")


if __name__ == "__main__":
    H.run(
        "photos",
        "Is the Google Photos appendonly export flow (uploads + mediaItems:batchCreate) still available?",
        body,
        gate="NO-GO = drop the Photos button, ship zip / Web Share only (already the P0 "
        "tier, so no replan). Never blocks anything.",
    )
