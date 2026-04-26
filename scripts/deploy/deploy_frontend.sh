#!/usr/bin/env bash
# Build the Next.js dashboard and deploy to Firebase Hosting (production).
#
# Usage:
#   ./scripts/deploy/deploy_frontend.sh

set -euo pipefail

PROJECT_ALIAS="${PROJECT_ALIAS:-production}"

echo "==> building frontend (next build → out/)"
pushd frontend >/dev/null
npm install --no-audit --no-fund
npm run build
popd >/dev/null

if [[ ! -d frontend/out ]]; then
  echo "error: frontend/out missing — next.config.js must use output: 'export'"
  exit 1
fi

echo "==> deploying hosting to alias '$PROJECT_ALIAS'"
# Note: Firestore rules NOT deployed here. Server libraries (Cloud Run) bypass
# rules and use IAM. The static dashboard does no client-side Firestore reads.
# Rules-as-code lives in firebase/firestore.rules for emulator dev only.
firebase deploy \
  --only hosting \
  --project "$PROJECT_ALIAS"
