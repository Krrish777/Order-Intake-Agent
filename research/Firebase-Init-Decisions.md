---
type: research-decision
topic: "Firebase CLI init choices for Sprint 1"
sprint: 1
date: 2026-04-20
tags:
  - firebase
  - firestore
  - emulator
  - setup
---

# Firebase Init Decisions — Sprint 1

Snapshot of what to select when running `firebase init` for the Order Intake Agent, why, and what is deferred. Decided 2026-04-20, before Track V seed-data load.

## One-line answer

**Select only `Firestore` in `firebase init`. Then run `firebase init emulators` separately. Do not set up a default project — use the `demo-order-intake-local` emulator convention.**

## Feature-by-feature verdict

The CLI 15.x menu shows seven features. Verdict for each in our context:

| Feature | Select? | Reason |
|---|---|---|
| Firestore | **yes** | ERP substitute per `CLAUDE.md`. Writes `firestore.rules`, `firestore.indexes.json`, plus the `firestore` block in `firebase.json`. Needed for every sprint track (V validation, P persistence, D dashboard). |
| Emulators | **yes — separately** | Not listed in the main menu in CLI 15.x. Run `firebase init emulators` as a second command. We need the Firestore emulator for local seed-data iteration; matches the "no live data risk, no quota burn" posture. |
| Hosting | no (defer to Track D) | Dashboard is a static SPA per `Glacis-Agent-Reverse-Engineering-Dashboard-UI.md`. `firebase init hosting` is trivial to add later when Track D starts. Enabling it now just creates an unused `public/` dir. |
| App Hosting | no | Meant for full-stack SSR apps (Next.js etc.). Our dashboard is static + Firestore listeners. |
| Functions | no | Agent runs on **Cloud Run** per `Glacis-Agent-Reverse-Engineering-Deployment.md`, not Cloud Functions. No function code in scope. |
| Storage | no | Gmail attachment download is explicitly deferred (`Order-Intake-Sprint-Status.md` row 1 — "Gmail API deferred"). Inject-CLI reads fixtures from the local file system. |
| Genkit | no | We use **ADK + Gemini** directly via `google-adk>=1.31` (already in `pyproject.toml`). Genkit is a separate framework we explicitly are not using. |
| SQL Connect | no | Firestore-only per `CLAUDE.md`. |

## Project-ID strategy

`firebase init` will ask whether to set up a default project. **Answer: no default project.**

Instead, hand-write `.firebaserc`:

```json
{ "projects": { "default": "demo-order-intake-local" } }
```

**Why the `demo-` prefix:** the Firestore emulator accepts any project ID starting with `demo-` without that project existing in the Firebase/GCP console. This lets us iterate on seed data with zero console provisioning, zero auth, zero quota. When we're ready to deploy live, we run `firebase use --add <real-project-id>` and the same loader code works unchanged (the client auto-detects emulator vs live via `FIRESTORE_EMULATOR_HOST`).

## Emulator-init prompts (second command)

`firebase init emulators` prompts:

- **Emulators to set up:** Firestore (+ optionally Emulator UI — recommended for the spot-check at Sprint Status verify step).
- **Firestore emulator port:** default `8080`.
- **Emulator UI port:** default `4000`.
- **Enable emulator UI:** yes.
- **Download emulators now:** yes.

This adds an `emulators` block to `firebase.json`. Nothing else changes.

## Files that will land in the repo

| File | Source | Commit? |
|---|---|---|
| `firebase.json` | `firebase init` (both commands) | yes |
| `firestore.rules` | `firebase init firestore` | yes — default deny-all is fine while the backend uses privileged creds |
| `firestore.indexes.json` | `firebase init firestore` | yes |
| `.firebaserc` | hand-written (demo project ID) | yes |
| `.firebase/` cache dir | auto-created by CLI | no (already in `.gitignore` conventionally) |

Verify `.firebase/` is gitignored before committing.

## Python client choice (already locked)

`google-cloud-firestore==2.27.0`, added to `pyproject.toml` on 2026-04-20. Chosen over `firebase-admin` because:

- Backend is Firestore-only — no need for the Auth/Storage/FCM bundle.
- Identical behavior against emulator and live via `FIRESTORE_EMULATOR_HOST`.
- Smaller dep surface.

If Track D's dashboard later needs Firebase Auth on the backend for session verification, revisit — adding `firebase-admin` alongside is fine, not either/or.

## What gets added post-sprint

When Track D starts (dashboard), re-run:

```bash
firebase init hosting
```

Pick:
- Public directory: `frontend/dist` (or wherever the SPA build lands).
- Single-page app: yes (rewrites all URLs to `/index.html`).
- GitHub auto-deploys: no (decide later).

When moving to live Firestore:

```bash
firebase projects:create order-intake-agent-prod   # or use an existing one
firebase use --add                                 # alias it as "prod"
gcloud auth application-default login              # so google-cloud-firestore picks up ADC
unset FIRESTORE_EMULATOR_HOST                      # make sure we're not still pointing at the emulator
GOOGLE_CLOUD_PROJECT=order-intake-agent-prod uv run python scripts/load_master_data.py
```

## Connections

- [Order-Intake-Sprint-Status](Order-Intake-Sprint-Status.md) — the sprint plan this init supports.
- `Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Overview.md` — the architecture diagram showing Firestore as the ERP substitute.
- `Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Firestore-Schema.md` — the collection design this loader seeds.
- `scripts/load_master_data.py` — the loader that reads these init artifacts (via env vars, not config files).
