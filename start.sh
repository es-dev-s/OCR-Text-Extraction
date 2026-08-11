#!/bin/sh
set -eu

PORT="${PORT:-8000}"
echo "pdf-extract-api starting host=0.0.0.0 port=${PORT}"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips '*' \
  --log-level info
