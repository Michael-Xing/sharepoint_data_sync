# Use Python 3.12 slim image
FROM m.daocloud.io/docker.io/library/python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Use an application-owned directory for uv cache to avoid permission issues
ENV UV_CACHE_DIR=/app/.uv-cache

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    libmariadb-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Set work directory
WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY uv.lock ./
COPY src/ ./src/
COPY main.py ./
COPY README.md ./

# Install Python dependencies
RUN uv sync --frozen --no-install-project --no-dev

# Create non-root user and prepare directories
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/data /app/logs /app/.uv-cache \
    && chown -R app:app /app
USER app

# Expose port (if needed for health checks)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command
CMD ["uv", "run", "python", "main.py"]


