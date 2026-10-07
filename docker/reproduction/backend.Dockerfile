FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
COPY requirements-dev.lock pyproject.toml alembic.ini /app/
RUN pip install --no-cache-dir -r requirements-dev.lock && pip check
COPY src /app/src
COPY migrations /app/migrations
COPY tests /app/tests
COPY scripts /app/scripts
COPY skills /app/skills
COPY data /app/data
COPY docs /app/docs
CMD ["python", "scripts/start-reproduction.py"]
