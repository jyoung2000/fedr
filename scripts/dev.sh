#!/usr/bin/env bash
# Run the backend locally (no Docker). Defaults to SIMULATION mode against ./data
set -euo pipefail
cd "$(dirname "$0")/.."
export FEDR_DEFAULT_MODE="${FEDR_DEFAULT_MODE:-simulation}"
export FEDR_GATEWAY_ENABLED="${FEDR_GATEWAY_ENABLED:-false}"
export FEDR_DATA_DIR="${FEDR_DATA_DIR:-$PWD/data}"
export FEDR_BIND="${FEDR_BIND:-127.0.0.1}"
export FEDR_PORT="${FEDR_PORT:-8935}"
cd backend && exec python -m fedr.main
