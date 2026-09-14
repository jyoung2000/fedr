#!/usr/bin/env bash
# Build the React UI and place it where the backend serves it (backend/fedr/static).
set -euo pipefail
cd "$(dirname "$0")/../frontend"
npm ci --no-audit --no-fund
npm run build
rm -rf ../backend/fedr/static
cp -r dist ../backend/fedr/static
echo "UI built into backend/fedr/static"
