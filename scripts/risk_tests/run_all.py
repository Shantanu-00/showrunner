"""Run every B1 risk probe and regenerate `artifacts/RESULTS.md`.

Order matters: `banana` generates the synthetic cast portrait that `veo` and `face_run`
both consume, so it runs before them. `face_run` reads an already-deployed Cloud Run
service and is skipped unless `--face` is passed, because deploying it costs ~7 minutes of
Cloud Build (see `face_run.py`'s docstring for the deploy command).

    python scripts/risk_tests/run_all.py              # the five API probes
    python scripts/risk_tests/run_all.py --face       # all six (service must be deployed)
    python scripts/risk_tests/run_all.py --only lyria armor

Probes are individually re-runnable and each one persists its own verdict, so a crash in
one never loses another's result. Total measured spend for a full pass is well under $1;
`veo` dominates it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness as H

HERE = Path(__file__).resolve().parent

# banana before veo/face_run: it produces the shared cast portrait they both need.
DEFAULT = ["armor", "photos", "banana", "lyria", "veo"]
NEEDS_DEPLOY = ["face_run"]


def main() -> int:
    argv = sys.argv[1:]
    if "--only" in argv:
        probes = argv[argv.index("--only") + 1:]
    else:
        probes = DEFAULT + (NEEDS_DEPLOY if "--face" in argv else [])

    if not probes:
        print("nothing to run")
        return 1

    print(f"running {len(probes)} probe(s): {', '.join(probes)}")
    for name in probes:
        script = HERE / f"{name}.py"
        if not script.exists():
            print(f"\n!! no such probe: {name} ({script.name} missing)")
            continue
        subprocess.run([sys.executable, str(script)], cwd=H.ROOT, check=False)

    report = H.artifact("RESULTS.md")
    print(f"\nreport: {report.relative_to(H.ROOT).as_posix()}")
    if "face_run" not in probes:
        print("note: face_run was skipped — pass --face once the probe service is deployed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
