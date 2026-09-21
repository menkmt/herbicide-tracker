# Tracker backend.
#
# tesseract and poppler are installed because scanned county permits are the
# normal case, not an edge case: without OCR a large share of the source
# documents are unreadable.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
        libpq5 \
        gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY . .

# Run as a non-root user; the importer executes OCR on untrusted uploads.
RUN useradd --create-home --uid 10001 tracker \
    && mkdir -p /app/storage \
    && chown -R tracker:tracker /app
USER tracker

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
