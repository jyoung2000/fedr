# Base images are build arguments so air-gapped / mirrored-registry deployments can substitute them
# (e.g. --build-arg BASE_IMAGE=registry.local/python:3.11-slim-bookworm). Defaults are the official images.
ARG BASE_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# ---------- frontend build ----------
FROM ${NODE_IMAGE} AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- python dependencies + package ----------
FROM ${BASE_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Build tools for the few dependencies without wheels; set APT_PACKAGES="" when the base image already has them.
ARG APT_PACKAGES="build-essential libffi-dev"
RUN if [ -n "$APT_PACKAGES" ]; then apt-get update && apt-get install -y --no-install-recommends $APT_PACKAGES \
    && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY backend/ ./
RUN python -m pip install .

# ---------- dev target (hot reload, tests) ----------
FROM base AS dev
RUN python -m pip install pytest pytest-asyncio hypothesis ruff
COPY --from=frontend /ui/dist ./fedr/static
CMD ["uvicorn", "fedr.main:app", "--host", "0.0.0.0", "--port", "8935"]

# ---------- production ----------
FROM ${BASE_IMAGE} AS prod
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# unprivileged runtime user (uid/gid 10001); skipped when the base image already provides it
RUN (getent group fedr >/dev/null || groupadd -r fedr --gid 10001) \
    && (id -u fedr >/dev/null 2>&1 || useradd -r -g fedr --uid 10001 --home-dir /app --shell /usr/sbin/nologin fedr) \
    && mkdir -p /data /app && chown fedr:fedr /data /app
WORKDIR /app
# site-packages location of the base image's interpreter (python:3.11-slim); override for other bases
ARG PY_SITE=/usr/local/lib/python3.11/site-packages
COPY --from=base ${PY_SITE} ${PY_SITE}
COPY --from=base /usr/local/bin /usr/local/bin
COPY --chown=fedr:fedr backend/fedr ./fedr
COPY --from=frontend --chown=fedr:fedr /ui/dist ./fedr/static
USER fedr
EXPOSE 8935
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8935/api/system/health', timeout=5).status==200 else 1)"
CMD ["python", "-m", "fedr.main"]
