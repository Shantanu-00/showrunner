"""Print the golden-fixture eval table — the judge-facing "how do you know the agents work" answer.

Reads `eval/artifacts/seed_run.json` (written by `python backend/seed.py --event demo`), re-fetches
each seeded media doc live from Firestore, grades it against `eval/fixtures.py`'s expectations, and
prints one row per fixture. This is a report, not a gate (CLAUDE.md: no unit tests, no TDD) — it
exits non-zero only if the seed run is missing or a fixture never reached the pipeline at all, since
those are infrastructure problems rather than a model call landing differently than expected.

    python eval/run_eval.py
    python eval/run_eval.py --run eval/artifacts/seed_run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent
BACKEND = EVAL.parents[0] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(EVAL))

import fixtures as fixtures_module  # noqa: E402
from shared import fs  # noqa: E402
from shared.settings import settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RUN_FILE = EVAL / "artifacts" / "seed_run.json"


def main() -> int:
    settings()

    ap = argparse.ArgumentParser(description="Grade the seeded golden fixtures.")
    ap.add_argument("--run", type=Path, default=RUN_FILE)
    args = ap.parse_args()

    if not args.run.exists():
        print(f"FAIL  no seed run at {args.run} — run `make seed` first")
        return 1

    run = json.loads(args.run.read_text(encoding="utf-8"))
    event_id = run["eventId"]
    event = fs.get_event(event_id) or {}
    glossary = set((event.get("eventTypeProfile") or {}).get("culturalGlossary") or [])

    # Built from the cast cache, not `run["cast"]`: a member whose *enrollment* failed (a live
    # network hiccup, not a fixture problem — see docs/context/friction-log.md 2026-08-28) still
    # had its portrait uploaded as two golden fixtures, and both deserve grading.
    import cast as cast_module  # noqa: E402 - deferred: only needed on this path, not for a stub

    cast_members = cast_module.ensure_cast()
    fixture_by_id = {f.fixtureId: f for f in fixtures_module.build_fixtures(cast_members)}

    print(f"Showrunner eval — event {event_id}, seeded {run.get('seededAt')}")
    print(f"{'fixture':<26} {'checks':<8} {'result'}")
    print("-" * 70)

    total_checks = total_passed = 0
    fixtures_ok = fixtures_missing = 0
    rows: list[dict[str, Any]] = []

    for item in run.get("items") or []:
        fixture_id = item["fixtureId"]
        fixture = fixture_by_id.get(fixture_id)
        media_id = item.get("mediaId")

        if not media_id or fixture is None:
            fixtures_missing += 1
            print(f"{fixture_id:<26} {'0/0':<8} ERROR  never reached the pipeline ({item.get('error', 'no fixture def')})")
            rows.append({"fixtureId": fixture_id, "error": item.get("error") or "no fixture definition"})
            continue

        doc = fs.media_ref(event_id, media_id).get().to_dict() or {}
        checks = fixtures_module.evaluate(fixture, doc, glossary)
        passed = sum(1 for c in checks if c.passed)
        total_checks += len(checks)
        total_passed += passed
        status = "PASS" if passed == len(checks) else "WARN"
        if status == "PASS":
            fixtures_ok += 1
        print(f"{fixture_id:<26} {f'{passed}/{len(checks)}':<8} {status}")
        for c in checks:
            if not c.passed:
                print(f"      - {c.name}: {c.detail}")
        rows.append(
            {
                "fixtureId": fixture_id,
                "mediaId": media_id,
                "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
            }
        )

    total_fixtures = len(run.get("items") or [])
    print("-" * 70)
    print(
        f"{fixtures_ok}/{total_fixtures} fixtures fully green, "
        f"{total_passed}/{total_checks} individual checks passed, "
        f"{fixtures_missing} never reached the pipeline"
    )

    report_path = EVAL / "artifacts" / "eval_report.json"
    report_path.write_text(
        json.dumps(
            {
                "eventId": event_id,
                "fixturesOk": fixtures_ok,
                "fixturesTotal": total_fixtures,
                "checksPassed": total_passed,
                "checksTotal": total_checks,
                "fixturesMissing": fixtures_missing,
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"report written to {report_path}")

    return 1 if fixtures_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
