# Multi-stage Dockerfile for Job Search Orchestrator
# Optimized for production deployment with minimal image size

# Stage 1: Build dependencies
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies to a virtual environment
RUN uv venv /app/.venv && \
    uv pip install -e . && \
    uv pip install pytest pytest-cov pytest-asyncio

# Stage 2: Runtime image
FROM python:3.12-slim as runtime

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/

# Copy application files
COPY src/ ./src/
COPY README.md CASE_STUDY.md ./

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOB_SCRAPER_DB="sqlite:///app/data/job_tracker.db" \
    METRICS_DIR="/app/metrics"

# Create directories for data and metrics
RUN mkdir -p /app/data /app/metrics && \
    chown -R app:app /app

# Switch to non-root user
USER app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from job_scraper.models import init_db; init_db()" || exit 1

# Default command - run the pipeline
ENTRYPOINT ["job-scraper"]
CMD ["--help"]
