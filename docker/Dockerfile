# syntax=docker/dockerfile:1.7
# ---------- frontend build ----------
FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- python base ----------
FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libffi-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/pyproject.toml ./
RUN pip install --upgrade pip && pip install . 2>/dev/null || true
COPY backend/ ./
RUN pip install --no-deps . && pip install $(python -c "import tomllib;print(' '.join(f'\"{d}\"' for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" | xargs)

# ---------- dev target ----------
FROM base AS dev
RUN pip install pytest pytest-asyncio hypothesis ruff
COPY --from=frontend /ui/dist ./fedr/static
CMD ["uvicorn", "fedr.main:app", "--host", "0.0.0.0", "--port", "8935"]

# ---------- production ----------
FROM python:3.11-slim-bookworm AS prod
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd -r fedr --gid 10001 && useradd -r -g fedr --uid 10001 --home-dir /app --shell /usr/sbin/nologin fedr \
    && mkdir -p /data && chown fedr:fedr /data
WORKDIR /app
COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --chown=fedr:fedr backend/fedr ./fedr
COPY --from=frontend --chown=fedr:fedr /ui/dist ./fedr/static
USER fedr
EXPOSE 8935
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8935/api/system/health', timeout=5).status==200 else 1)"
CMD ["python", "-m", "fedr.main"]
