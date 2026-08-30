"""`worker-video-prep` — ffprobe + poster + keyframes + proxy, then the ordinary photo fan-out.

The only B2 worker that reads the raw bucket and the only one that enqueues downstream work, because
it is the only one that *produces* renders rather than consuming them (spec 03 §4).
"""
