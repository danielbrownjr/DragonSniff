FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DRAGONSNIFF_BIND=0.0.0.0 \
    DRAGONSNIFF_PORT=8765 \
    DRAGONSNIFF_DATA_DIR=/data \
    DRAGONSNIFF_REQUIRE_ALLOWLIST=1

RUN addgroup --system dragonsniff \
    && adduser --system --ingroup dragonsniff --home /nonexistent --no-create-home dragonsniff

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN mkdir /data && chown dragonsniff:dragonsniff /data
USER dragonsniff:dragonsniff
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"]

ENTRYPOINT ["dragonsniff"]
