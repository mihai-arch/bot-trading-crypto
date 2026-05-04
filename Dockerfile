# BIT — crypto trading bot
#
# Single image used for both services:
#   - bot runner:  python -m bit
#   - dashboard:   uvicorn bit.dashboard.app:app --host 0.0.0.0 --port 8765
#
# Build:
#   docker build -t bit .
#
# Run runner:
#   docker run --env-file .env -v $(pwd)/data:/app/data bit python -m bit
#
# Run dashboard:
#   docker run --env-file .env -v $(pwd)/data:/app/data -p 8765:8765 bit \
#       uvicorn bit.dashboard.app:app --host 0.0.0.0 --port 8765

FROM python:3.11-slim

WORKDIR /app

# Install dependencies before copying source so Docker layer cache is reused
# when only source files change.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[dashboard]"

# Persistent data directory — mount a volume here in production.
RUN mkdir -p /app/data

# Default: run the bot. Override in docker-compose.yml or with --command.
CMD ["python", "-m", "bit"]
