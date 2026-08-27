# One image, three services (api / intake / dlq) — selected at runtime by $SERVICE, see main.py.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so a code-only change reuses the layer cache in Cloud Build.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . ./

ENV PORT=8080 SERVICE=api
# One worker per container: Cloud Run scales by instance, and a second worker would just double
# the memory for the same concurrency.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
