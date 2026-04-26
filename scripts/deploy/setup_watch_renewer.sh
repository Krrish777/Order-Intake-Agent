#!/usr/bin/env bash
# Deploy the Cloud Run Job that calls Gmail users.watch() and the daily
# Cloud Scheduler trigger that runs it.
#
# Usage:
#   ./scripts/deploy/setup_watch_renewer.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-order-intake-agent-491911}"
REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-gmail-watch-renewer}"
SCHEDULER_JOB="${SCHEDULER_JOB:-gmail-watch-renew-daily}"
RUNTIME_SA="order-intake-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> deploying Cloud Run Job: $JOB_NAME (gcloud run jobs deploy supports --source)"
gcloud run jobs deploy "$JOB_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command uv \
  --args "run,--no-sync,python,scripts/renew_watch.py" \
  --update-secrets "GMAIL_REFRESH_TOKEN=gmail-refresh-token:latest,GMAIL_CLIENT_ID=gmail-client-id:latest,GMAIL_CLIENT_SECRET=gmail-client-secret:latest" \
  --set-env-vars "GMAIL_PUBSUB_PROJECT_ID=${PROJECT_ID},GMAIL_PUBSUB_TOPIC=${GMAIL_PUBSUB_TOPIC:-gmail-inbox-events}"

echo "==> creating Cloud Scheduler trigger (daily at 04:00 UTC)"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_NUMBER}/jobs/${JOB_NAME}:run"

if gcloud scheduler jobs describe "$SCHEDULER_JOB" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  SCHED_ACTION=update
else
  SCHED_ACTION=create
fi

gcloud scheduler jobs "$SCHED_ACTION" http "$SCHEDULER_JOB" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --schedule "0 4 * * *" \
  --uri "$JOB_URI" \
  --http-method POST \
  --oauth-service-account-email "$RUNTIME_SA"

echo
echo "==> running the job once now to start the watch"
gcloud run jobs execute "$JOB_NAME" --region "$REGION" --project "$PROJECT_ID" --wait
