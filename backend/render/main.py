"""Entrypoint for the `render` Cloud Run Job — one execution, one commission.

    python -m render.main --event dev_demo --reel 01J...

The arguments arrive as a container override on `run_job` (`shared/jobs.py`), which is how a Cloud Run
Job is parameterised per execution: there is no request body, and the job's baked `args` are the
fallback for a manual `gcloud run jobs execute`.

**The exit code is the contract with Cloud Run.** A non-zero exit makes the execution `Failed`, which is
what shows up on the job's console page and what a retry policy would act on — so it is reserved for
*infrastructure* failures (the reel document does not exist, the environment is unconfigured). A
commission that ran and could not produce a film exits **0**: it wrote `status='failed'` and an `ops/`
alert, which is the product's own record, and marking the execution failed as well would mean a red
console entry for a correct outcome — the same distinction `shared/pipeline.py` draws between a stage
that broke and a stage whose answer was "no".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from directors.reel import pipeline
from shared import log
from shared.settings import settings

log.configure("render")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="render", description="Render one commissioned reel.")
    parser.add_argument("--event", required=True, help="eventId")
    parser.add_argument("--reel", required=True, help="reelId of an existing commission")
    args = parser.parse_args(argv)

    cfg = settings()
    cfg.require("project", "derived_bucket", "curated_bucket")

    try:
        report = asyncio.run(pipeline.run(args.event, args.reel))
    except Exception as exc:  # noqa: BLE001 - the pipeline handles its own failures; this is the rest
        log.error("render_job_failed", event_id=args.event, reel_id=args.reel, err=str(exc))
        return 1

    # One line of JSON on stdout: it is what `scripts/smoke_reel.py` reads when it runs the job locally,
    # and what a `gcloud logging read` on the execution shows without expanding anything.
    print(json.dumps(report.as_dict(), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
