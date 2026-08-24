FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies first for layer caching, then the
# Playwright-managed Chromium with its system dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  && playwright install --with-deps chromium \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Loopback-only unless the operator explicitly opts into container-network
# access with ROVER_BIND_HOST=0.0.0.0. docker-compose.yml does that internally
# while still publishing the host port on 127.0.0.1 only.
CMD ["sh", "-c", "exec python3 -m hypercorn main:app --bind \"${ROVER_BIND_HOST:-127.0.0.1}:8000\""]
