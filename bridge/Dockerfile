FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[remote]" && rm -rf /app/build /app/src/*.egg-info

# limit damage if compromised
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bridge
USER 10001

ENV JOBSEER_BIND_HOST=0.0.0.0 \
    JOBSEER_BIND_PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

ENTRYPOINT ["jobseer-bridge", "serve"]
