---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Cloud Run and Firebase Deployment"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 5
date: 2026-04-08
tags:
  - research
  - supply-chain
  - deployment
  - cloud-run
  - firebase-hosting
  - docker
  - ci-cd
---

# Cloud Run and Firebase Deployment

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 5. Parent: [[Glacis-Agent-Reverse-Engineering-Build-Plan]]

## The Problem

Your ADK agents work on localhost. The dashboard renders on `localhost:3000`. Firestore is populated, Pub/Sub topics are created, Gmail API is connected. The end-to-end demo runs from your laptop. None of this matters for the hackathon submission. The judges need a live URL they can visit and a demo video showing a deployed system. "It works on my machine" is not a deliverable.

The deployment target is Google's ecosystem — Cloud Run for the backend, Firebase Hosting for the frontend, with Firestore, Pub/Sub, Gmail API, and Gemini already configured. The constraint is that everything must be deployable in one day (Day 15 of the [[Glacis-Agent-Reverse-Engineering-Build-Plan|build plan]]) and remain running with zero operational attention through the submission window and evaluation period. You cannot babysit a server during judging.

The secondary constraint is cost. Google Cloud's free tier is generous but has limits. Cloud Run gives you 180,000 vCPU-seconds/month (50 hours of compute), 2 million requests/month, and 1 GB of egress. Firestore gives you 50,000 reads and 20,000 writes per day. These limits are more than sufficient for a hackathon demo that processes dozens of orders, not thousands. But exceeding them — through a misconfigured polling loop or a runaway Gemini call — generates real charges. The deployment must be cost-aware.

## First Principles

A deployment architecture for this system has five components. Each maps to exactly one Google Cloud service.

```
┌─────────────────────────────────────────────────┐
│                    Internet                       │
└────────┬──────────────────────────┬───────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐    ┌────────────────────────┐
│ Firebase Hosting │    │      Cloud Run          │
│   (Dashboard)    │───▶│   (FastAPI + ADK)       │
│   Static SPA     │    │   Agent endpoints       │
│   /api/* proxy   │    │   Webhook handlers      │
└─────────────────┘    └─────────┬──────────────┘
                                 │
                    ┌────────────┼────────────────┐
                    │            │                 │
                    ▼            ▼                 ▼
            ┌─────────┐  ┌───────────┐    ┌──────────────┐
            │Firestore │  │  Pub/Sub  │    │  Gmail API   │
            │  (Data)  │  │ (Events)  │    │ (Email I/O)  │
            └─────────┘  └───────────┘    └──────────────┘
                                │
                                ▼
                        ┌──────────────┐
                        │Cloud Scheduler│
                        │ (PO Timers)  │
                        └──────────────┘
```

**Firebase Hosting** serves the static dashboard SPA. It handles HTTPS termination, CDN caching, and custom domain provisioning. Requests to `/api/*` are rewritten to the Cloud Run backend via Firebase's built-in reverse proxy — no NGINX configuration, no CORS issues, one domain for everything.

**Cloud Run** hosts the FastAPI application that contains both the ADK agent logic and the API endpoints the dashboard calls. It is the single backend service. It receives Pub/Sub push messages (email events, PO follow-up triggers), processes them through the agent pipeline, writes results to Firestore, and sends emails via the Gmail API. Scale-to-zero means zero cost when nobody is using it. Cold start adds 3-8 seconds to the first request — acceptable for an email processing pipeline where latency is measured in minutes, not milliseconds.

**Firestore** is the database for everything: master data, transactional state, agent session history, and the real-time data source for the dashboard. The dashboard uses Firestore's `onSnapshot` listeners for live updates — when the agent writes a new order, the dashboard updates within 1-2 seconds without polling.

**Pub/Sub** decouples email ingestion from agent processing. Gmail API push notifications arrive at a Pub/Sub topic. Cloud Run subscribes to that topic. This means a burst of 50 emails does not overwhelm the agent — Pub/Sub buffers and delivers at the rate Cloud Run can process. It also means the Gmail watcher and the agent can deploy independently.

**Cloud Scheduler** fires at intervals (every 30 minutes or hourly) to check for overdue PO confirmations. It publishes a message to the `po-confirmation` Pub/Sub topic, which triggers the PO Confirmation agent to scan for POs past their SLA deadline and send follow-up emails.

## The Backend: Cloud Run

### Project Structure

```
backend/
├── agents/
│   ├── __init__.py
│   ├── order_intake/
│   │   ├── __init__.py
│   │   └── agent.py          # ADK Order Intake agent definition
│   └── po_confirmation/
│       ├── __init__.py
│       └── agent.py          # ADK PO Confirmation agent definition
├── api/
│   ├── __init__.py
│   ├── routes/
│   │   ├── orders.py         # /api/orders endpoints
│   │   ├── exceptions.py     # /api/exceptions endpoints
│   │   ├── metrics.py        # /api/metrics endpoints
│   │   └── webhooks.py       # Pub/Sub push handler, Gmail webhook
│   └── dependencies.py       # Firestore client, shared deps
├── services/
│   ├── extraction.py         # Gemini extraction calls
│   ├── validation.py         # Business rule validation
│   ├── email_service.py      # Gmail API send/receive
│   └── firestore_service.py  # Firestore CRUD operations
├── config/
│   └── settings.py           # Pydantic settings from env vars
├── main.py                   # FastAPI app + ADK integration
├── requirements.txt
├── Dockerfile
└── .dockerignore
```

### Dockerfile

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for any binary Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

# Copy application code
COPY . .

USER appuser

# Cloud Run injects PORT env var (defaults to 8080)
ENV PORT=8080
EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
```

The single worker (`--workers 1`) is deliberate. Cloud Run scales by adding container instances, not by running multiple workers inside one container. Multiple workers inside one container waste memory. Let Cloud Run handle concurrency at the instance level.

The `python:3.13-slim` base image is 45 MB versus 350 MB for the full image. On Cloud Run, image size directly affects cold start time — every 100 MB adds roughly 0.5-1 seconds to cold start. A slim image cold-starts in 3-5 seconds. A full image cold-starts in 6-10 seconds. For an email processing pipeline, this difference is irrelevant in production. For a live demo where the judge clicks a button and waits, it matters.

### requirements.txt

```
google-adk>=1.0.0
fastapi[standard]>=0.115.0
uvicorn>=0.32.0
google-cloud-firestore>=2.19.0
google-cloud-pubsub>=2.25.0
google-api-python-client>=2.150.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
pydantic-settings>=2.6.0
```

### main.py (ADK + FastAPI Integration)

The ADK documentation provides a `get_fast_api_app` helper that serves the ADK development UI alongside your agents. For the hackathon, this gives you a free debugging interface. For the production demo, your custom dashboard replaces it.

```python
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Option A: ADK's built-in FastAPI app (includes dev UI)
from google.adk.cli.fast_api import get_fast_api_app

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    session_service_uri="firestore://",  # Use Firestore for sessions
    allow_origins=["*"],
    web=True,  # Serve ADK dev UI at root
)

# Mount your custom API routes alongside ADK
from api.routes import orders, exceptions, metrics, webhooks
app.include_router(orders.router, prefix="/api")
app.include_router(exceptions.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("PORT", 8080)))
```

### Environment Variables and Secrets

Two categories. Non-sensitive configuration goes in environment variables set during deployment. Sensitive values go in Secret Manager and are injected by Cloud Run at startup.

**Environment Variables (non-sensitive):**

| Variable | Value | Purpose |
|----------|-------|---------|
| `GOOGLE_CLOUD_PROJECT` | `your-project-id` | Project identifier |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Region (use free tier region) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | Use Vertex AI for Gemini (recommended for Cloud Run) |
| `FIRESTORE_DATABASE` | `(default)` | Firestore database name |
| `PUBSUB_ORDER_TOPIC` | `order-intake` | Topic for incoming orders |
| `PUBSUB_PO_TOPIC` | `po-confirmation` | Topic for PO events |
| `GMAIL_WATCH_INBOX` | `orders@yourdomain.com` | Gmail inbox to monitor |

**Secrets (via Secret Manager):**

| Secret Name | Purpose | How Set |
|-------------|---------|---------|
| `GOOGLE_API_KEY` | Gemini API key (if not using Vertex AI) | `gcloud secrets create` |
| `GMAIL_OAUTH_TOKEN` | Gmail API OAuth refresh token | OAuth flow during setup |
| `GMAIL_CLIENT_SECRET` | Gmail API client secret | GCP Console download |

Create secrets:

```bash
# Create the secret
echo -n "your-api-key-here" | gcloud secrets create GOOGLE_API_KEY \
    --project=$GOOGLE_CLOUD_PROJECT \
    --data-file=-

# Grant Cloud Run's service account access
gcloud secrets add-iam-policy-binding GOOGLE_API_KEY \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/secretmanager.secretAccessor" \
    --project=$GOOGLE_CLOUD_PROJECT
```

Reference secrets in the deploy command:

```bash
gcloud run deploy glacis-agent \
    --source . \
    --region us-central1 \
    --project $GOOGLE_CLOUD_PROJECT \
    --allow-unauthenticated \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=True" \
    --set-secrets="GOOGLE_API_KEY=GOOGLE_API_KEY:latest,GMAIL_OAUTH_TOKEN=GMAIL_OAUTH_TOKEN:latest" \
    --memory=1Gi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=3 \
    --timeout=300
```

The `--min-instances=0` enables scale-to-zero (free when idle). The `--max-instances=3` prevents runaway scaling and unexpected charges. The `--timeout=300` (5 minutes) gives Gemini calls time to process large documents. The `--memory=1Gi` is sufficient for a single-worker Python process with ADK loaded.

### Alternative: ADK CLI Deployment

If the manual Dockerfile approach has issues, the ADK CLI provides a one-command deployment:

```bash
adk deploy cloud_run \
    --project=$GOOGLE_CLOUD_PROJECT \
    --region=us-central1 \
    --service_name=glacis-agent \
    --app_name=glacis_agents \
    --with_ui \
    ./agents
```

This builds the container, pushes to Artifact Registry, and deploys to Cloud Run automatically. The `--with_ui` flag includes the ADK development interface — useful for debugging but not a substitute for your custom dashboard. The tradeoff: less control over Dockerfile internals, but deployment in 2 minutes instead of 20.

### Pub/Sub Push Subscription

Gmail API push notifications and Cloud Scheduler triggers arrive as Pub/Sub messages. Cloud Run receives these as HTTP POST requests to a webhook endpoint.

```bash
# Create the push subscription
gcloud pubsub subscriptions create order-intake-push \
    --topic=order-intake \
    --push-endpoint=https://glacis-agent-HASH-uc.a.run.app/api/webhooks/pubsub \
    --ack-deadline=60 \
    --project=$GOOGLE_CLOUD_PROJECT
```

The webhook handler in FastAPI:

```python
import base64
import json
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

@router.post("/webhooks/pubsub")
async def handle_pubsub(request: Request):
    """Handle Pub/Sub push messages."""
    envelope = await request.json()
    if not envelope or "message" not in envelope:
        raise HTTPException(status_code=400, detail="Invalid Pub/Sub message")

    message = envelope["message"]
    data = json.loads(base64.b64decode(message["data"]).decode())

    # Route based on message attributes or topic
    event_type = message.get("attributes", {}).get("event_type", "unknown")

    if event_type == "gmail_notification":
        await process_gmail_notification(data)
    elif event_type == "po_followup_check":
        await process_po_followup(data)
    else:
        # Log and ack unknown messages (don't nack — prevents infinite retry)
        logger.warning(f"Unknown event type: {event_type}")

    return {"status": "ok"}  # 200 = ack
```

Returning a 200 status code acknowledges the message. Returning 4xx or 5xx causes Pub/Sub to retry with exponential backoff. For the hackathon, always return 200 — a retry storm from unhandled messages will blow through your free tier.

### Cloud Scheduler for PO Follow-ups

```bash
gcloud scheduler jobs create http po-followup-check \
    --schedule="0 */2 * * *" \
    --uri="https://glacis-agent-HASH-uc.a.run.app/api/webhooks/po-check" \
    --http-method=POST \
    --oidc-service-account-email=$SERVICE_ACCOUNT_EMAIL \
    --project=$GOOGLE_CLOUD_PROJECT \
    --location=us-central1
```

This fires every 2 hours. The handler queries Firestore for POs where `status == "sent"` and `sent_date < now() - SLA_HOURS`. For each overdue PO, it triggers the follow-up email generation pipeline.

For the hackathon demo, you might want to trigger this manually instead of waiting 2 hours:

```bash
gcloud scheduler jobs run po-followup-check --location=us-central1
```

## The Frontend: Firebase Hosting

### Project Structure

```
frontend/
├── public/
│   └── index.html
├── src/
│   ├── App.jsx
│   ├── components/
│   │   ├── OrderList.jsx
│   │   ├── ExceptionQueue.jsx
│   │   ├── POTracker.jsx
│   │   ├── MetricsDashboard.jsx
│   │   └── Layout.jsx
│   ├── hooks/
│   │   └── useFirestore.js    # onSnapshot real-time listeners
│   ├── services/
│   │   └── api.js             # Fetch calls to /api/*
│   └── firebase.js            # Firebase SDK initialization
├── firebase.json
├── .firebaserc
├── package.json
└── vite.config.js
```

### firebase.json

This is the critical configuration file. The `rewrites` section routes API requests to Cloud Run while serving the SPA for everything else.

```json
{
  "hosting": {
    "public": "dist",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      {
        "source": "/api/**",
        "run": {
          "serviceId": "glacis-agent",
          "region": "us-central1"
        }
      },
      {
        "source": "**",
        "destination": "/index.html"
      }
    ],
    "headers": [
      {
        "source": "/api/**",
        "headers": [
          {
            "key": "Cache-Control",
            "value": "no-cache, no-store, must-revalidate"
          }
        ]
      },
      {
        "source": "**/*.@(js|css|svg|png)",
        "headers": [
          {
            "key": "Cache-Control",
            "value": "public, max-age=31536000, immutable"
          }
        ]
      }
    ]
  }
}
```

The first rewrite sends `/api/**` requests to the `glacis-agent` Cloud Run service. Firebase Hosting handles the proxying — no CORS configuration needed because everything appears to come from the same domain. The second rewrite is the SPA catch-all: any route that is not a static file or API call returns `index.html`, letting React Router handle client-side routing.

The `Cache-Control` headers are significant. API responses get `no-cache` — stale data in a real-time dashboard is worse than no cache. Static assets get `immutable` with a 1-year max-age — Vite's content-hashed filenames make this safe. This setup means the dashboard loads fast (cached JS/CSS) while always showing current data (uncached API calls).

### Deployment Commands

```bash
# Build the React app
cd frontend
npm run build

# Initialize Firebase (first time only)
firebase init hosting
# Select existing project, set public directory to "dist"

# Deploy
firebase deploy --only hosting --project $GOOGLE_CLOUD_PROJECT
```

The deployed URL will be `https://your-project-id.web.app` or `https://your-project-id.firebaseapp.com`. Both work. For the hackathon submission, either URL is the "prototype link."

### Local Development

During development, you do not want to deploy to Cloud Run on every code change. Use the Firebase emulator to proxy API requests to your local FastAPI server.

```json
// firebase.json (add for local dev)
{
  "emulators": {
    "hosting": {
      "port": 5000
    }
  }
}
```

Run the local stack:

```bash
# Terminal 1: Backend
cd backend && uvicorn main:app --reload --port 8080

# Terminal 2: Frontend dev server (for hot reload)
cd frontend && npm run dev

# Terminal 3: Firebase emulator (optional, for testing hosting rewrites)
firebase emulators:start --only hosting
```

For most development, just run the backend on port 8080 and the Vite dev server on port 5173 with a proxy configuration in `vite.config.js`:

```javascript
// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      }
    }
  }
})
```

This mirrors the production Firebase Hosting rewrite behavior: `/api/*` goes to the backend, everything else is served by Vite.

## Cost Estimate

The free tier covers a hackathon demo comfortably. Here is the breakdown for a system processing 50-100 orders during the demo + evaluation period.

| Service | Free Tier | Expected Usage | Cost |
|---------|-----------|----------------|------|
| Cloud Run | 180K vCPU-sec, 2M requests/mo | ~1,000 requests, ~5,000 vCPU-sec | $0.00 |
| Firestore | 50K reads, 20K writes/day | ~2,000 reads, ~500 writes/day | $0.00 |
| Pub/Sub | 10 GiB/month | <1 GiB/month | $0.00 |
| Firebase Hosting | 10 GB storage, 360 MB/day transfer | <100 MB storage, <50 MB/day | $0.00 |
| Gemini API (via Vertex AI) | Varies by model | ~200 calls at $0.01-0.05 each | $2-10 |
| Cloud Scheduler | 3 jobs free | 1 job | $0.00 |
| Secret Manager | 6 active versions free | 3 secrets | $0.00 |
| Artifact Registry | 500 MB free storage | ~200 MB container image | $0.00 |
| **Total** | | | **$2-10/month** |

The only non-free cost is Gemini API inference. If you use the Google AI Studio API key instead of Vertex AI, there is a generous free tier for Gemini Flash (1,500 requests/day) and Gemini Pro (50 requests/day as of early 2026). For a hackathon demo, the free tier is sufficient if you use Flash for classification and routine extraction, reserving Pro for complex documents only.

**Cost traps to avoid:**
- A polling loop that queries Firestore every second from the dashboard. Use `onSnapshot` listeners instead — they use a single persistent connection, not repeated reads.
- A Pub/Sub subscription that nacks every message (returns 4xx/5xx). Pub/Sub retries with exponential backoff, generating more messages, generating more Cloud Run invocations, generating more Pub/Sub messages. Always ack messages, even if processing fails.
- Forgetting to set `--max-instances=3` on Cloud Run. Without a cap, a traffic spike (or a retry storm) can spin up unlimited instances.
- Leaving Cloud Scheduler running at high frequency after the demo. An every-minute schedule generates 43,200 invocations per month. Every-2-hours generates 360. Use the lowest frequency that makes the demo work.

## CI/CD with GitHub Actions (Optional)

For a hackathon, manual deployment with `gcloud run deploy` and `firebase deploy` is sufficient. If you want automated deployment on git push — and it does impress judges who check your GitHub — here is the minimal setup.

```yaml
# .github/workflows/deploy.yml
name: Deploy to Google Cloud

on:
  push:
    branches: [main]

jobs:
  deploy-backend:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Deploy to Cloud Run
        run: |
          cd backend
          gcloud run deploy glacis-agent \
            --source . \
            --region us-central1 \
            --project ${{ secrets.GCP_PROJECT_ID }} \
            --allow-unauthenticated \
            --quiet

  deploy-frontend:
    runs-on: ubuntu-latest
    needs: deploy-backend
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Build
        run: |
          cd frontend
          npm ci
          npm run build

      - uses: FirebaseExtended/action-hosting-deploy@v0
        with:
          repoToken: ${{ secrets.GITHUB_TOKEN }}
          firebaseServiceAccount: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}
          channelId: live
          projectId: ${{ secrets.GCP_PROJECT_ID }}
```

This uses Workload Identity Federation (WIF) for keyless authentication — no service account key files stored in GitHub Secrets. Setting up WIF takes 15 minutes the first time but is the Google-recommended approach. If time is short, a service account key JSON in GitHub Secrets works but is less secure.

## Docker-Compose for Local Testing

For local development without deploying to Cloud Run, a docker-compose setup lets all three team members run the same environment.

```yaml
# docker-compose.yml
version: "3.9"

services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    environment:
      - GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
      - FIRESTORE_EMULATOR_HOST=firestore:8081
      - PUBSUB_EMULATOR_HOST=pubsub:8085
    depends_on:
      - firestore
      - pubsub

  firestore:
    image: google/cloud-sdk:slim
    command: >
      gcloud emulators firestore start
      --host-port=0.0.0.0:8081
      --project=${GOOGLE_CLOUD_PROJECT}
    ports:
      - "8081:8081"

  pubsub:
    image: google/cloud-sdk:slim
    command: >
      gcloud beta emulators pubsub start
      --host-port=0.0.0.0:8085
      --project=${GOOGLE_CLOUD_PROJECT}
    ports:
      - "8085:8085"

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.dev
    ports:
      - "3000:3000"
    environment:
      - VITE_API_URL=http://localhost:8080
    volumes:
      - ./frontend/src:/app/src
```

The Firestore and Pub/Sub emulators let you develop offline without hitting the real Google Cloud APIs. The `FIRESTORE_EMULATOR_HOST` and `PUBSUB_EMULATOR_HOST` environment variables tell the Google Cloud client libraries to use the local emulators instead of the production services. No code changes needed — the same client code works against emulators and production.

The caveat: the Firestore emulator does not support vector search (needed for embedding-based SKU matching). For that feature, either use the real Firestore instance during development or fall back to exact-match SKU lookup locally.

## The Tradeoffs

**`adk deploy cloud_run` vs manual Dockerfile.** The ADK CLI deployment is a single command and works out of the box. It generates a Dockerfile, builds the image, pushes to Artifact Registry, and deploys to Cloud Run. The cost is opacity — you cannot customize the Dockerfile, add system dependencies, or control the base image. If the agent code has any dependency that requires a system package (like `libmagic` for file type detection), the ADK CLI build will fail silently. Start with `adk deploy cloud_run`. If it works, ship it. If it fails, fall back to the manual Dockerfile where you control everything.

**Firebase Hosting rewrites vs separate domains.** The rewrite approach (`/api/**` proxied to Cloud Run) gives you a single domain for the entire application. The alternative is separate domains — `glacis-agent-HASH.a.run.app` for the API and `your-project.web.app` for the dashboard — with CORS headers to allow cross-origin requests. The single-domain approach is simpler: no CORS, no mixed-content issues, cleaner URLs for the demo. The separate-domain approach is simpler to set up initially (no firebase.json rewrites). For the hackathon, the Firebase Hosting rewrite is worth the 5 minutes of configuration.

**Scale-to-zero vs minimum instances.** `--min-instances=0` means the first request after idle triggers a cold start (3-8 seconds). `--min-instances=1` keeps one instance warm at all times, eliminating cold starts but costing ~$8/month for a 1 vCPU instance. For the demo video, you warm the system with a test request before recording. For the judges who visit the prototype link, a 5-second cold start is acceptable — they are clicking a URL, not running a latency benchmark. Use `--min-instances=0`.

**Vertex AI vs Google AI Studio for Gemini.** Vertex AI integrates natively with Cloud Run IAM — no API key needed, just service account permissions. Google AI Studio requires an API key stored in Secret Manager. Vertex AI has slightly different pricing and rate limits. Google AI Studio has a more generous free tier for development. For deployment on Cloud Run, Vertex AI is cleaner (no secrets to manage for the Gemini call). For development, Google AI Studio is faster to set up. Use AI Studio locally, Vertex AI in Cloud Run.

**Emulators vs production services for development.** The Firestore and Pub/Sub emulators are free and offline-capable. But they do not perfectly replicate production behavior — no vector search, no real-time listeners across machines, no IAM. For a team of 3, use emulators for individual development and the real production Firestore for integration testing. Create a `dev` Firestore database (Firestore supports multiple named databases per project) to isolate development data from demo data.

## What Most People Get Wrong

**Deploying on the last day.** Deployment is not a final step. It is Week 3, Day 15 of a 4-week plan. Every day you test only on localhost is a day you accumulate deployment-specific bugs: missing environment variables, wrong IAM permissions, Firestore security rules that block reads, Pub/Sub push endpoints that reject messages because Cloud Run is not authenticated. Deploy on Day 15. Fix what breaks on Days 16-18. Record the demo video on Days 20-21. If you deploy on Day 25 and it breaks, you have zero buffer.

**Hardcoding localhost URLs.** Every URL in the codebase must come from an environment variable. Firestore connections, Pub/Sub topics, API endpoints called by the frontend — all of them. A hardcoded `http://localhost:8080` in the React app works perfectly in development and fails silently in production (the browser sends the request to its own localhost, which is not your server). Use `import.meta.env.VITE_API_URL` in the frontend and `os.environ` in the backend.

**Ignoring cold start in the demo.** The demo video must not start with the judge waiting 8 seconds for a cold start. Before recording, send a warmup request to the Cloud Run service. Before submission, include a note that the first request may be slow. Better yet, trigger the warmup request as part of the demo script — "Let me first load the dashboard" (which triggers a warmup to the API).

**Storing secrets in environment variables.** The Google Cloud best practice is explicit: do not store sensitive values in environment variables. Use Secret Manager. The `--set-secrets` flag in `gcloud run deploy` injects Secret Manager values as environment variables at container startup — the application reads `os.environ["GOOGLE_API_KEY"]` as normal, but the value is not visible in the Cloud Run configuration, not logged in deployment output, and not stored in your repository. This matters less for a hackathon than for production, but judges who inspect your deployment configuration will notice.

**Not setting max-instances.** Cloud Run defaults to 100 maximum instances. If a Pub/Sub retry storm or a curious judge's automated testing tool hits your service with 1,000 concurrent requests, Cloud Run will scale to handle them — and charge you for every instance-second. Set `--max-instances=3`. For a hackathon demo, 3 concurrent instances is generous. The free tier covers 180,000 vCPU-seconds per month. At 3 instances running continuously, that is only 16.7 hours. Cap it.

## Connections

- [[Glacis-Agent-Reverse-Engineering-Build-Plan]] — The 4-week plan that schedules deployment for Day 15. This note provides the Day 15 implementation details.
- [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] — Pub/Sub topic design, message schemas, and subscription patterns. The `gcloud pubsub subscriptions create` commands here implement those designs.
- [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] — Collection schemas that the backend reads and writes. The Firestore security rules reference these schemas.
- [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] — Firestore-as-ERP pattern. This deployment note implements the infrastructure that note describes.
- [[Glacis-Agent-Reverse-Engineering-Overview]] — The full research map. This is note 27 of 27.
- [[cloud-run]] — Wiki page on Cloud Run fundamentals: revision-based deploys, scale-to-zero, concurrency model.
- [[firebase]] — Wiki page on Firebase ecosystem: Hosting, Auth, real-time sync.
- [[pub-sub]] — Wiki page on Pub/Sub: topics, subscriptions, push vs pull, dead letter queues.
- [[docker]] — Wiki page on Docker fundamentals: images, layers, multi-stage builds.

## References

### Google Cloud Documentation
- [Deploy Python FastAPI to Cloud Run — Google Cloud](https://docs.cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-fastapi-service) — `gcloud run deploy --source .` quickstart, buildpack auto-detection
- [ADK Cloud Run Deployment — Google ADK Docs](https://adk.dev/deploy/cloud-run/) — `adk deploy cloud_run` command, Dockerfile, `main.py` with `get_fast_api_app`, Secret Manager configuration, environment variables
- [Firebase Hosting + Cloud Run — Google Firebase](https://firebase.google.com/docs/hosting/cloud-run) — Rewrite configuration in `firebase.json`, proxying requests to Cloud Run services
- [Configure Hosting Behavior — Firebase](https://firebase.google.com/docs/hosting/full-config) — Complete `firebase.json` reference: rewrites, redirects, headers, i18n
- [Configure Secrets for Cloud Run Services — Google Cloud](https://docs.cloud.google.com/run/docs/configuring/services/secrets) — `--set-secrets` flag, Secret Manager integration, IAM bindings
- [Secret Manager Best Practices — Google Cloud](https://docs.cloud.google.com/secret-manager/docs/best-practices) — Pin versions, automatic replication, minimal IAM, never use env vars for secrets

### Deployment Guides
- [How to Deploy FastAPI on Cloud Run — OneUptime (Feb 2026)](https://oneuptime.com/blog/post/2026-02-17-how-to-deploy-a-fastapi-application-on-cloud-run-with-automatic-api-documentation/view) — Step-by-step with Dockerfile, Artifact Registry, environment configuration
- [Building Modern API with FastAPI, Firestore, Cloud Run — DevOps With Dave](https://devopswithdave.com/gcp/firestore/fastapi/github%20actions/post-fastapi-modern-api/) — Full stack: FastAPI + Firestore + Cloud Run + GitHub Actions CI/CD
- [Deploy FastAPI with Firebase Hosting + Firestore — Medium](https://medium.com/@schnaror/deploy-a-django-or-fastapi-application-using-firebase-hosting-and-firestore-part-1-0b4c08a17469) — Firebase Hosting fronting a Python backend

### Pricing
- [Cloud Run Pricing — Google Cloud](https://cloud.google.com/run/pricing) — Free tier: 180K vCPU-sec, 360K GiB-sec, 2M requests/month in us-central1/us-east1/us-west1
- [Firestore Pricing — Google Cloud](https://cloud.google.com/firestore/pricing) — Free tier: 1 GB storage, 50K reads, 20K writes, 20K deletes per day
- [Firebase Pricing — Google](https://firebase.google.com/pricing) — Hosting free tier: 10 GB storage, 360 MB/day transfer
