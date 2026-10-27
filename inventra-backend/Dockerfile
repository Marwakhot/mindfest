# Inventra backend. Python 3.9 matches the version the test suite was run on.
FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUNTIME_DIR=/app/runtime \
    WATCHER_POLLING=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY inventra ./inventra
COPY data ./data
COPY generator ./generator
COPY tests ./tests

# Run as an unprivileged user; code and seed data are read-only to it, only runtime/ is writable
RUN useradd --create-home --uid 10001 inventra \
    && mkdir -p /app/runtime/inbox /app/runtime/archive /app/runtime/quarantine \
    && chown -R inventra:inventra /app/runtime
USER inventra

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/settings/mode', timeout=4).status == 200 else 1)"

# 0.0.0.0 inside the container only; docker-compose publishes it on the host's 127.0.0.1
CMD ["python", "-m", "uvicorn", "inventra.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
