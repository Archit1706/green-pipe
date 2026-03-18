FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer)
COPY pyproject.toml ./
COPY src/__init__.py src/__init__.py
RUN pip install --no-cache-dir .

# Copy full source
COPY . .

EXPOSE 8000

CMD uvicorn src.api.routes:app --host 0.0.0.0 --port ${PORT:-8000}
