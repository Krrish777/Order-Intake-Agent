#!/usr/bin/env bash
# Wire the Gmail Pub/Sub topic to push to the Cloud Run service.
#
# Replaces the existing PULL subscription with a push subscription that
# authenticates with the runtime service account's OIDC token.
#
# Usage:
#   ./scripts/deploy/setup_pubsub_push.sh https://order-intake-pubsub-xyz.a.run.app

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <cloud-run-url>"
  exit 1
fi

SERVICE_URL="$1"
PROJECT_ID="${PROJECT_ID:-order-intake-agent-491911}"
TOPIC="${GMAIL_PUBSUB_TOPIC:-gmail-inbox-events}"
SUBSCRIPTION="${GMAIL_PUBSUB_PUSH_SUBSCRIPTION:-order-intake-push-sub}"
OLD_PULL_SUB="${GMAIL_PUBSUB_SUBSCRIPTION:-order-intake-ingestion}"
RUNTIME_SA="order-intake-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
PUSH_ENDPOINT="${SERVICE_URL%/}/pubsub/push"

echo "==> retiring old pull subscription if present"
gcloud pubsub subscriptions delete "$OLD_PULL_SUB" --project "$PROJECT_ID" --quiet 2>/dev/null \
  || echo "    (no $OLD_PULL_SUB to delete)"

echo "==> creating push subscription $SUBSCRIPTION -> $PUSH_ENDPOINT"
if gcloud pubsub subscriptions describe "$SUBSCRIPTION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "$SUBSCRIPTION" \
    --project "$PROJECT_ID" \
    --push-endpoint="$PUSH_ENDPOINT" \
    --push-auth-service-account="$RUNTIME_SA" \
    --push-auth-token-audience="$SERVICE_URL" \
    --ack-deadline=600
  echo "    UPDATED"
else
  gcloud pubsub subscriptions create "$SUBSCRIPTION" \
    --project "$PROJECT_ID" \
    --topic="$TOPIC" \
    --push-endpoint="$PUSH_ENDPOINT" \
    --push-auth-service-account="$RUNTIME_SA" \
    --push-auth-token-audience="$SERVICE_URL" \
    --ack-deadline=600
  echo "    CREATED"
fi

echo
echo "==> next: trigger the watch renewer once to start receiving notifications"
echo "    ./scripts/deploy/setup_watch_renewer.sh"
