#!/usr/bin/env bash
# Build + deploy the Cloud Run service that handles Gmail Pub/Sub push.
#
# Run setup_gcp.sh first. Re-run this script for every code change you
# want live. Cloud Run keeps the previous revision around so rollback is
# `gcloud run services update-traffic ... --to-revisions=PREV=100`.
#
# Usage:
#   ./scripts/deploy/deploy_run.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-order-intake-agent-491911}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-order-intake-pubsub}"
RUNTIME_SA="order-intake-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

PUBSUB_TOPIC="${GMAIL_PUBSUB_TOPIC:-gmail-inbox-events}"
PROCESSED_LABEL="${GMAIL_PROCESSED_LABEL:-orderintake-processed}"
GMAIL_QUERY_DEFAULT="in:inbox label:order-intake"

echo "==> deploying $SERVICE to $REGION"
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300s \
  --max-instances 5 \
  --update-secrets "GMAIL_REFRESH_TOKEN=gmail-refresh-token:latest,GMAIL_CLIENT_ID=gmail-client-id:latest,GMAIL_CLIENT_SECRET=gmail-client-secret:latest,GOOGLE_API_KEY=google-api-key:latest,LLAMA_CLOUD_API_KEY=llama-cloud-api-key:latest" \
  --set-env-vars "GMAIL_PUBSUB_PROJECT_ID=${PROJECT_ID},GMAIL_PUBSUB_TOPIC=${PUBSUB_TOPIC},GMAIL_PROCESSED_LABEL=${PROCESSED_LABEL},GMAIL_QUERY=${GMAIL_QUERY:-${GMAIL_QUERY_DEFAULT}},GMAIL_SEND_DRY_RUN=1"

SERVICE_URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo
echo "==> deployed: $SERVICE_URL"
echo
echo "Next: ./scripts/deploy/setup_pubsub_push.sh \"$SERVICE_URL\""
