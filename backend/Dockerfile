FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev iputils-ping curl && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 -r requirements.txt

# Application code
COPY . .

RUN chmod +x docker-entrypoint.sh && \
    groupadd --system --gid 1001 appgroup && \
    useradd --system --uid 1001 --gid appgroup appuser && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
