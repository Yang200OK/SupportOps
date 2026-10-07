FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
COPY requirements-dev.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY src /app/src
RUN useradd --create-home --uid 10001 relaydesk
USER relaydesk
