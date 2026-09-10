FROM python:3.12-slim

# No .pyc anywhere: the container runs with a read-only root filesystem, so a
# stray write attempt is a crash rather than a cache.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

RUN useradd --uid 1000 --create-home jobscout
USER 1000

ENTRYPOINT ["python", "-m", "jobscout"]
CMD ["run"]
