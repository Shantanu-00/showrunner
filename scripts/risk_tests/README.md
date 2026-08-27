# B1 risk probes

Six scripts, each answering one GO/NO-GO question about a platform capability the design
already depends on. They exist because every one of these is a place where the docs, the
SDK type hints and the actual API disagree — and finding that out during B1 costs minutes,
while finding it out during B5 costs the submission.

These are **not tests** (`CLAUDE.md`: no unit tests, no TDD). A probe asserts nothing. It
calls the real API on the real project and records what happened, including the exact
error text when the platform refuses — because that error text is the finding.

## Run

```bash
gcloud auth application-default login        # once; ADC, never key files
python scripts/risk_tests/run_all.py         # the five API probes (~5 min, <$1)
python scripts/risk_tests/banana.py          # or any single probe
python scripts/risk_tests/veo.py --image path/to/your.jpg
```

Output lands in `artifacts/` (gitignored): `results.json`, a pasteable `RESULTS.md`, and
the generated media. Every probe is idempotent and safe to re-run.

## The six

| Probe | Question | If it goes red |
|---|---|---|
| `lyria` | Does Lyria 3 return usable audio for a reel soundtrack? | Ship licensed stock beds; reels lose the generated-score story |
| `veo` | Does Veo 3.1 Fast turn a still into a vertical opener clip? | Drop the AI opener; reels start on a real photo |
| `banana` | Will Nano Banana edit a photo **containing a person**? | Already P2 — restyling is a bonus surface, not a path |
| `armor` | Does Model Armor sanitize a prompt injection in guest text? | Fall back to a Gemini-based screen; slower and less defensible |
| `face_run` | Does InsightFace serve on Cloud Run, baked and fast, at 512-d unit norm? | **The one real emergency.** Escalate; do not work around |
| `photos` | Is the Photos `appendonly` export flow still available? | Ship the P0 zip / Web Share tier; no replan |

`face_run` is the odd one out: it reads a Cloud Run service it does not deploy, because the
build takes minutes. Deploy it first, then pass `--face`:

```bash
gcloud run deploy showrunner-face-probe \
  --source scripts/risk_tests/face_probe \
  --region us-central1 --project showrunner-hq \
  --cpu 2 --memory 4Gi --min-instances 0 --max-instances 2 --concurrency 4 \
  --timeout 300 --no-allow-unauthenticated --set-env-vars MODEL_ROOT=/models

python scripts/risk_tests/run_all.py --face

# probe-only resource — delete it once the numbers are recorded
gcloud run services delete showrunner-face-probe --region us-central1 --quiet
```

## Notes on how these are written

- **No real faces.** `banana.py` generates a photoreal portrait of a synthetic person once;
  `veo` and `face_run` reuse it. No guest's biometric data goes through a probe. Override
  with `--image <path>` if you want to probe with your own photo.
- **`_harness.py` never raises.** A probe blowing up is itself a result, so exceptions are
  captured as `ERROR` verdicts with the traceback attached, and one probe's crash never
  loses another's verdict.
- **Every probe records its own gate.** The verdict alone is useless six hours later; what
  matters is the pre-committed decision that follows from it, which is why `gate=` is
  written before the probe runs, not after the result is known.
- **The durable record is elsewhere.** `artifacts/RESULTS.md` is a local convenience.
  Verdicts go into HANDOFF §9 and platform surprises into
  `docs/context/friction-log.md`, both of which survive a fresh clone.
