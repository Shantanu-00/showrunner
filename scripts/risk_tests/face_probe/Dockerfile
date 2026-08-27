# InsightFace-on-Cloud-Run probe container.
#
# The three things this is proving, in order of how badly they'd hurt on B2:
#   1. `insightface` + `onnxruntime` install at all on a slim Python 3.11 image
#      (insightface ships Cython sources and needs a compiler at build time).
#   2. The 326 MB buffalo_l model can be BAKED AT BUILD, not downloaded at startup —
#      spec 09 sizes worker-face at min-instances 1 precisely because a runtime download
#      would put a ~30 s+ cold start in front of the first guest photo.
#   3. The baked model loads and embeds on Cloud Run's CPU inside our latency budget.
FROM python:3.11-slim

# insightface builds native extensions; opencv needs libGL/libglib at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libgl1 \
      libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the model into the image.
#
# insightface 1.0.1 IGNORES the INSIGHTFACE_HOME env var — it resolves the model dir from
# the `root=` kwarg only (default `~/.insightface`, so a bake without it lands in
# /root/.insightface and is invisible to a non-root runtime user). Pass root explicitly,
# and pass the SAME value at runtime via MODEL_ROOT or the worker re-downloads on boot.
ENV MODEL_ROOT=/models
RUN python -c "\
import os, insightface;\
app = insightface.app.FaceAnalysis(name='buffalo_l', root=os.environ['MODEL_ROOT'], providers=['CPUExecutionProvider']);\
app.prepare(ctx_id=-1, det_size=(640, 640));\
print('buffalo_l baked')"

# Fail the build loudly if the bake landed somewhere the runtime won't look. Keep this
# line: without it, a mis-rooted bake ships silently and only shows up as a slow service.
RUN test -d "$MODEL_ROOT/models/buffalo_l" && du -sh "$MODEL_ROOT/models/buffalo_l"

COPY main.py .

ENV PORT=8080
CMD ["python", "main.py"]
