#!/usr/bin/env bash
# One-time GCP project bootstrap for Order Intake Agent.
#
# Idempotent — safe to re-run. Enables APIs, creates the runtime service
# account, grants minimum IAM, and uploads the 5 secrets from local .env
# into Secret Manager.
#
# Prereqs:
#   - gcloud CLI authenticated (`gcloud auth login`)
#   - Local .env populated with the 5 secret values
#
# Usage:
#   ./scripts/deploy/setup_gcp.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-order-intake-agent-491911}"
REGION="${REGION:-us-central1}"
RUNTIME_SA="order-intake-runtime"
RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "==> using project: $PROJECT_ID, region: $REGION"
gcloud config set project "$PROJECT_ID"

echo "==> enabling APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  cloudscheduler.googleapis.com \
  aiplatform.googleapis.com \
  gmail.googleapis.com

echo "==> creating runtime service account (idempotent)"
gcloud iam service-accounts create "$RUNTIME_SA" \
  --display-name="Order Intake Agent runtime" \
  2>/dev/null || echo "    already exists"

# Wait for SA to be visible to IAM (eventual-consistency race).
echo "==> waiting for service account to propagate"
for i in 1 2 3 4 5 6; do
  if gcloud iam service-accounts describe "$RUNTIME_SA_EMAIL" >/dev/null 2>&1; then
    echo "    visible after ${i}x"
    break
  fi
  sleep 5
done

echo "==> granting roles to $RUNTIME_SA_EMAIL"
for role in \
  roles/datastore.user \
  roles/secretmanager.secretAccessor \
  roles/pubsub.subscriber \
  roles/aiplatform.user \
  roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
  echo "    granted $role"
done

echo "==> grant Pub/Sub service agent the token-creator role (push OIDC)"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PUBSUB_SA}" \
  --role=roles/iam.serviceAccountTokenCreator \
  --condition=None \
  --quiet >/dev/null
echo "    granted roles/iam.serviceAccountTokenCreator to $PUBSUB_SA"

echo "==> uploading secrets from .env"
if [[ ! -f .env ]]; then
  echo "    .env not found — skipping secret upload (create them manually later)"
  exit 0
fi

create_or_update_secret() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "    SKIP $name (empty in .env)"
    return
  fi
  if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    printf "%s" "$value" | gcloud secrets versions add "$name" --data-file=- --project="$PROJECT_ID" >/dev/null
    echo "    UPDATE $name (new version)"
  else
    printf "%s" "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project="$PROJECT_ID" >/dev/null
    echo "    CREATE $name"
  fi
}

# Pull values from .env (strip quotes, ignore comments)
get_env() {
  local key="$1"
  grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' || true
}

create_or_update_secret gmail-client-id     "$(get_env GMAIL_CLIENT_ID)"
create_or_update_secret gmail-client-secret "$(get_env GMAIL_CLIENT_SECRET)"
create_or_update_secret gmail-refresh-token "$(get_env GMAIL_REFRESH_TOKEN)"
create_or_update_secret google-api-key      "$(get_env GOOGLE_API_KEY)"
create_or_update_secret llama-cloud-api-key "$(get_env LLAMA_CLOUD_API_KEY)"

echo "==> done. Next: ./scripts/deploy/deploy_run.sh"
