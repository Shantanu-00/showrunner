"""The `render` Cloud Run Job (spec 09 §1: 8 vCPU / 32 GiB, one task per commission).

Its own package, and its own image (`backend/docker/Dockerfile.render`), because ffmpeg, the fonts and
`librosa<1.0` are ~400 MB that api/intake/the perception workers must never carry — the same reasoning
that gave `worker-face` its own image (friction log 2026-08-27: a 1049 MB image cost a 29.6 s cold
start, and `intake` scales 0→20 on a burst).
"""
