FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Healthcheck: simple HTTP GET on /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3   CMD python - <<'PY' || exit 1
import os, sys, urllib.request, json
url = f"http://127.0.0.1:{os.environ.get('PORT','8080')}/health"
try:
    with urllib.request.urlopen(url, timeout=3) as r:
        data = json.loads(r.read().decode())
        sys.exit(0 if data.get("ok") else 1)
except Exception:
    sys.exit(1)
PY

CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
