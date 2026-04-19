---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "2-4 Week Build Plan for 3-Person Team"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 5
date: 2026-04-08
tags:
  - research
  - supply-chain
  - build-plan
  - hackathon
  - project-management
  - team
---

# 2-4 Week Build Plan for 3-Person Team

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 5. Parent: [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]

## The Problem

You have a working architecture on paper — 27 deep dive notes covering Order Intake, PO Confirmation, event-driven pipelines, Firestore schemas, ADK agents, and deployment patterns. The companion research set ([[Supply-Chain-Solution-Challenge-Overview]]) has another 33 notes covering strategic positioning, hackathon submission requirements, and competitive differentiation. That is 60 notes of research. Zero lines of code.

The Google Solution Challenge 2026 timeline is unforgiving. Registration closes April 15, 2026. Prototype submission deadline is April 24, 2026. That gives you approximately 16 days from today (April 8) to produce six deliverables: a working prototype, a problem statement, a solution overview, a project deck, a public GitHub repository, and a demo video. Phase 2 (Top 100 refinement) runs through May 28 with enhanced prototypes, and the Grand Finale happens the last week of June. Technical Merit carries 40% of the scoring weight. Innovation carries 25%. Alignment with theme carries 25%. UX carries 10%.

A 2-4 week build plan for a 3-person team must answer three questions. First, what is the minimum viable product that demonstrates the architecture? Not the full system — the slice that proves the concept works end-to-end. Second, how do you parallelize the work across three people so nobody blocks anybody else? Third, what do you cut when you inevitably fall behind?

## First Principles

A hackathon build is not a product build. The goal is not production robustness. The goal is a convincing demonstration of a working system that judges can evaluate against four criteria in under 3 minutes. Every engineering decision must be filtered through one question: does this make the demo better?

This means three architectural constraints.

**Constraint 1: Demo-driven scope.** The demo scenario ([[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]) defines the entire scope. If a feature does not appear in the 2-minute video walkthrough, it does not get built. If the demo shows Order Intake processing an email, then email ingestion, extraction, validation, and ERP write must work end-to-end. If the demo shows PO Confirmation sending a follow-up email, then PO monitoring, SLA detection, email generation, and supplier response parsing must work. Everything outside the demo path is overhead.

**Constraint 2: Vertical slice over horizontal coverage.** One agent working end-to-end beats three agents working halfway. The critical path is: email arrives → agent extracts structured data → validates against master data → creates/updates order in Firestore → sends confirmation/follow-up email. If that pipeline works with one email format (say, a plain-text email with a simple order), you have a demo. If it also handles PDF attachments, that is impressive. If it also handles Excel files, that is exceptional. But the plain-text-email-to-ERP path must work before anything else.

**Constraint 3: Firestore is the ERP.** There is no SAP to integrate with. Firestore serves triple duty: product/customer/supplier master data, transactional state (orders, POs, confirmations), and the dashboard's real-time data source. This simplifies the build enormously. Every "ERP integration" step in the architecture becomes a Firestore write. Every "master data lookup" becomes a Firestore read. The [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] note covers this pattern in detail.

## Team Role Split

Three people. Three domains. Minimal overlap on day-to-day work. Heavy overlap on integration points.

### Person 1: Agent/Backend Lead

Owns the ADK agent definitions, Gemini prompts, validation logic, and business rules. This person writes the code that makes the AI do things.

**Responsibilities:**
- ADK agent scaffolding (Order Intake agent, PO Confirmation agent, coordinator)
- Gemini prompt engineering for extraction, classification, and email generation
- Validation pipeline (price check, quantity check, SKU matching, delivery date feasibility)
- Exception routing logic (auto-execute / clarify / escalate decision tree)
- SOP playbook configuration schema
- Integration with Person 2's infrastructure (consuming Pub/Sub events, reading/writing Firestore)

**Skill requirements:** Python, FastAPI, basic understanding of ADK agent patterns, comfort with prompt engineering. This is the user's natural role given their Python/FastAPI background and existing ADK familiarity.

### Person 2: Infrastructure/Data Lead

Owns everything that is not agent logic and not UI. The plumbing that makes data flow.

**Responsibilities:**
- Google Cloud project setup (IAM, billing, service accounts, Secret Manager)
- Firestore data model implementation (collections, indexes, security rules)
- Synthetic data generation (products, customers, suppliers, orders, POs — see [[Glacis-Agent-Reverse-Engineering-Synthetic-Data]])
- Gmail API integration (OAuth setup, watch/push notification subscription, attachment download)
- Pub/Sub topic creation and Cloud Run subscriber configuration
- Cloud Run deployment pipeline (Dockerfile, `gcloud run deploy`, environment variables)
- Firebase Hosting setup for the dashboard SPA
- Cloud Scheduler jobs for PO follow-up triggers

**Skill requirements:** Google Cloud console familiarity, basic Python, comfort with infrastructure configuration. Does not need deep AI/ML knowledge.

### Person 3: Frontend/Demo Lead

Owns the dashboard UI, the demo video, the solution brief, and the project deck. This person makes the system visible.

**Responsibilities:**
- Dashboard UI (React or vanilla JS SPA on Firebase Hosting)
- Real-time order/PO status display (Firestore onSnapshot listeners)
- Exception queue with one-click approve/reject/edit interface
- Metrics visualization (touchless rate, processing time, exception breakdown)
- Demo scenario scripting and synthetic email preparation
- 2-minute demo video recording and editing
- Solution brief / project deck authoring
- GitHub README and repository structure

**Skill requirements:** JavaScript/React basics, video editing tool (Loom, OBS, or Canva), slide design. Does not need backend or cloud knowledge.

### Integration Points (Where People Must Coordinate)

| Integration | People | When | What Gets Agreed |
|-------------|--------|------|-----------------|
| Firestore schema | 1 + 2 | Day 2 | Collection names, document shapes, field types |
| Pub/Sub event contracts | 1 + 2 | Day 3 | Topic names, message schemas, who publishes/subscribes |
| Dashboard API | 1 + 3 | Day 5 | REST endpoints the dashboard calls, response shapes |
| Gmail integration handoff | 1 + 2 | Day 7 | How email events reach the agent, attachment handling |
| Demo script finalization | All | Day 14 | Exact sequence of actions for the 2-minute video |
| Deployment dry run | All | Day 18 | Full end-to-end on Cloud Run, not localhost |

## The 4-Week Plan

### Week 1: Foundation (Days 1-7)

The goal for Week 1 is zero-to-working-locally. By Friday of Week 1, a plain-text email should flow through the system end-to-end on localhost. Not pretty. Not robust. Working.

**Day 1 (All): Project Bootstrap**
- Create GitHub repository with directory structure: `agents/`, `api/`, `data/`, `ui/`, `tests/`, `docs/`, `scripts/`
- Person 1: Initialize FastAPI app with ADK dependency, create stub agent definition
- Person 2: Create Google Cloud project, enable APIs (Cloud Run, Firestore, Pub/Sub, Gmail, Secret Manager, Artifact Registry), set up service account
- Person 3: Initialize React app (Vite + React), create dashboard wireframes, start solution brief outline
- All: Agree on Firestore collection names and document shapes (30-minute sync)

**Day 2 (Parallel):**
- Person 1: Build Order Intake extraction prompt — take a hardcoded email string, send to Gemini, get structured JSON back. Test with 5 different email formats. Iterate prompt until extraction accuracy hits >90% on test cases.
- Person 2: Implement Firestore data model — `products`, `customers`, `suppliers`, `orders`, `purchase_orders`, `confirmations` collections. Write seed script that populates 20 products, 5 customers, 3 suppliers.
- Person 3: Build dashboard layout — header, sidebar nav, main content area. Wire up Firestore SDK. Display seed products in a table to confirm connectivity.

**Day 3 (Parallel):**
- Person 1: Build validation pipeline — price check against product master, quantity bounds check, SKU matching (start with exact match, add fuzzy later). Wire extraction output → validation → routing decision.
- Person 2: Set up Pub/Sub topics (`order-intake`, `po-confirmation`, `exceptions`, `notifications`). Create Cloud Run subscriber stubs that log received messages. Set up Gmail API OAuth credentials.
- Person 3: Build order list view — pull from `orders` collection, show status badges (pending, validated, confirmed, exception). Add real-time listener so new orders appear without refresh.

**Day 4 (Parallel):**
- Person 1: Build the routing decision tree — auto-execute path writes to Firestore, clarify path generates a clarification email draft, escalate path writes to exceptions queue. Use hardcoded thresholds (95% auto, 80-95% clarify, <80% escalate).
- Person 2: Implement Gmail API `watch()` subscription — push notifications to a Pub/Sub topic when emails arrive at the orders inbox. Handle `historyId` correctly (fetch history, then messages). Download attachments.
- Person 3: Build exception queue view — list exceptions with type, severity, original data, agent recommendation. Add approve/reject buttons that write resolution back to Firestore.

**Day 5 (Integration):**
- Person 1 + 2: Connect Gmail → Pub/Sub → Agent pipeline. When Person 2's Gmail handler publishes a message, Person 1's agent processes it. Test with a real email sent to the shared inbox.
- Person 3: Build dashboard API endpoints — `/api/orders`, `/api/exceptions`, `/api/metrics`. Wire dashboard to call these instead of reading Firestore directly (cleaner separation, easier to add auth later).
- All: 30-minute sync to demo what works, identify blockers.

**Day 6 (Parallel):**
- Person 1: Build confirmation email generation — after auto-execute, generate and send a confirmation email to the customer via Gmail API. Build clarification email generation for the clarify path.
- Person 2: Expand synthetic data — add 50 products with aliases, 10 customers with order history, 5 suppliers with PO history. Create test email fixtures (5 plain-text, 3 PDF, 2 Excel).
- Person 3: Build metrics dashboard panel — touchless rate, avg processing time, exception breakdown by type. Pull from Firestore aggregation queries.

**Day 7 (Integration + Milestone):**
- All: End-to-end test on localhost. Send an email → agent extracts → validates → writes order → sends confirmation. If this works, Week 1 is a success.
- Person 1: Document every prompt, every threshold, every decision rule in a `docs/agent-config.md`
- Person 2: Document every Firestore collection, every Pub/Sub topic, every environment variable in `docs/infrastructure.md`
- Person 3: Screenshot the working dashboard for the project deck

**Week 1 Exit Criteria:** A plain-text email sent to a Gmail inbox triggers agent processing, creates an order in Firestore, and appears on the dashboard. Confirmation email sent back. At least one exception type (price mismatch) correctly routes to the exception queue.

### Week 2: Core Agents (Days 8-14)

Week 2 is about depth. The Order Intake agent handles more formats, more edge cases, more exception types. The PO Confirmation agent comes online.

**Day 8 (Parallel):**
- Person 1: Add PDF attachment handling to Order Intake — pass PDFs to Gemini Pro multimodal, extract structured data. Test with 5 different PDF layouts (tabular, free-form, mixed).
- Person 2: Add embedding-based SKU matching — generate text embeddings for product descriptions using Gemini embedding API, store in Firestore vector fields, implement similarity search for fuzzy matching.
- Person 3: Polish exception queue — add inline editing (so the human can fix extracted data before approving), add "send clarification" button that triggers the clarify email flow.

**Day 9 (Parallel):**
- Person 1: Start PO Confirmation agent — monitor `purchase_orders` collection for status changes, detect when a PO has been sent but not confirmed within the SLA window.
- Person 2: Set up Cloud Scheduler job that fires every hour, checks for overdue POs, publishes follow-up events to the `po-confirmation` topic.
- Person 3: Add PO tracking view to dashboard — list of POs with status (sent, awaiting confirmation, confirmed, exception), days since sent, supplier name.

**Day 10 (Parallel):**
- Person 1: Build PO follow-up email generation — use templates from [[Glacis-Agent-Reverse-Engineering-Supplier-Communication]] (friendly, direct, firm escalation ladder). Generate professional follow-up emails via Gemini with quality filter.
- Person 2: Handle supplier response ingestion — when a supplier replies to a PO email, Gmail push notification triggers parsing. Route to PO Confirmation agent for extraction and validation.
- Person 3: Build supplier communication log — show all emails sent/received per PO, thread view, response timestamps.

**Day 11 (Parallel):**
- Person 1: Build PO confirmation extraction — extract confirmed quantities, prices, delivery dates from supplier response. Cross-reference against original PO. Flag discrepancies.
- Person 2: Implement the quality gate — secondary Gemini Flash call that validates every outbound email against: no hallucinated URLs, no hallucinated data, no unauthorized commitments, professional tone.
- Person 3: Add exception details modal — when clicking an exception, show full context: original email, extracted data, validation results, agent recommendation, approve/reject/edit actions.

**Day 12 (Integration):**
- All: Full integration test of PO Confirmation flow. Create a PO → wait for SLA → agent sends follow-up → simulate supplier reply → agent extracts confirmation → validates → updates ERP.
- Person 1: Fix extraction accuracy issues discovered during integration
- Person 2: Fix data flow issues (missing fields, wrong Pub/Sub message shapes)
- Person 3: Fix dashboard rendering issues, ensure PO and Order views both work

**Day 13 (Parallel):**
- Person 1: Add Excel/CSV attachment handling to Order Intake. Add multi-line-item extraction (orders with 5-10 line items, not just single items).
- Person 2: Create the SOP playbook Firestore collection — per-customer thresholds, per-exception-type routing rules, email templates. Populate with demo data.
- Person 3: Build SOP playbook editor view — display current rules, allow threshold editing, template preview. This is a judges-love-it feature that demonstrates configurability.

**Day 14 (Integration + Milestone):**
- All: End-to-end demo rehearsal. Run the full demo scenario: 3 orders (clean, clarification needed, exception), 2 POs (confirmed, discrepancy). Time it. It must fit in 2 minutes.
- Person 1: Tune prompts based on demo rehearsal failures
- Person 2: Fix any data integrity issues
- Person 3: Record a rough demo video for internal review

**Week 2 Exit Criteria:** Order Intake handles plain-text, PDF, and at least one other format. PO Confirmation sends follow-ups and processes responses. The demo scenario runs end-to-end in under 3 minutes. Both agents appear on the dashboard.

### Week 3: Polish and Deploy (Days 15-21)

Week 3 splits between deployment and demo quality. The system must work on Cloud Run, not just localhost.

**Day 15 (Deployment):**
- Person 2: Deploy backend to Cloud Run. Dockerfile, `gcloud run deploy`, environment variables via Secret Manager. Verify the agent processes a real email from Cloud Run (not localhost).
- Person 1: Fix any issues that appear only in Cloud Run (cold start latency, missing env vars, Firestore permissions).
- Person 3: Deploy dashboard to Firebase Hosting. Configure `firebase.json` rewrite rules to proxy `/api/*` to Cloud Run. Verify real-time Firestore listeners work from hosted dashboard.

**Day 16 (Parallel):**
- Person 1: Add the learning loop — when a human corrects an extraction or overrides a routing decision, capture the correction as a new alias in the product master or a rule update in the SOP playbook.
- Person 2: Set up monitoring — Cloud Run logs, Firestore usage metrics, Pub/Sub message counts. Create a simple health check endpoint.
- Person 3: Build the metrics summary panel — total orders processed, touchless rate percentage, avg processing time, exceptions resolved. This is the "impact" slide in the deck.

**Day 17 (Parallel):**
- Person 1: Edge case hardening — what happens when Gemini returns malformed JSON? When the email has no attachments? When the product master has no match? Add error handling for the top 5 failure modes.
- Person 2: Seed the production Firestore with the full demo dataset — 50 products, 10 customers, 5 suppliers, 20 historical orders (to show the metrics panel has data).
- Person 3: Write the solution brief. 2-page document: problem, solution, architecture diagram, Google technologies used, impact metrics, team.

**Day 18 (Integration):**
- All: Full deployment dry run. Run the entire demo scenario on the deployed system (Cloud Run + Firebase Hosting). Time it. Record it. Watch for failures.
- Fix whatever breaks. There will be something.

**Day 19 (Parallel):**
- Person 1: Final prompt tuning based on deployment dry run. Optimize for the specific demo emails that will be used in the video.
- Person 2: Cost audit — check Firestore reads/writes, Cloud Run invocations, Gemini API calls. Ensure the demo stays within free tier or has a known cost. Document in `docs/cost-estimate.md`.
- Person 3: Create the project deck (10-12 slides). Problem, solution, architecture, demo screenshots, tech stack, impact, team, future roadmap.

**Day 20-21 (Demo + Video):**
- Person 3: Record the final 2-minute demo video. Structure: 20s problem hook → 30s solution overview → 40s live demo (email arrives, agent processes, dashboard updates, PO follow-up, supplier confirmation) → 20s architecture diagram → 10s impact metrics.
- Person 1 + 2: Run the live system while Person 3 records. Reset data between takes. Be ready to resend demo emails.
- All: Review video. Re-record if needed. A clean single-take demo is better than flashy editing.

**Week 3 Exit Criteria:** System deployed to Cloud Run + Firebase Hosting. Demo video recorded. Solution brief complete. Project deck complete. GitHub README finalized with setup instructions.

### Week 4: Buffer and Submission (Days 22-28)

Week 4 exists because Week 3 will not go according to plan.

**Days 22-24: Buffer for overrun.** Whatever did not get done in Week 3 gets done here. If Week 3 went perfectly (it will not), use this time for polish: better error messages in the dashboard, more synthetic data variety, a second demo scenario.

**Day 25: Submission preparation.** Verify every deliverable: prototype link works, GitHub repo is public and documented, demo video is uploaded, solution brief is formatted, project deck is polished. Have someone outside the team try to access the prototype from the link.

**Day 26: Submit.** Submit before the April 24 deadline. Submit early if possible — submitting on the last day invites technical failures.

**Days 27-28: Breathe.** Phase 2 (Top 100 refinement) does not start until May 29. If selected, you have a month to improve. That month is when you add the features you cut: WhatsApp integration, advanced analytics, multi-language support, deeper exception handling.

## The Critical Path

This is the dependency chain where any delay propagates to the final deadline.

```
Day 1: Cloud project + Firestore schema
  → Day 2: Seed data + Extraction prompt
    → Day 5: Gmail → Agent pipeline (INTEGRATION)
      → Day 7: End-to-end on localhost (MILESTONE)
        → Day 12: PO Confirmation integration (MILESTONE)
          → Day 15: Cloud Run deployment (MILESTONE)
            → Day 18: Deployment dry run (MILESTONE)
              → Day 20-21: Demo video recording (MILESTONE)
                → Day 26: Submission
```

Every other task is parallel work that supports this chain. If the Day 5 Gmail-to-Agent integration fails, nothing downstream works. If the Day 15 Cloud Run deployment fails, the demo video cannot be recorded. Protect this chain ruthlessly.

## What to Cut When Behind

You will fall behind. The question is what to sacrifice. Here is the priority order, from "cut first" to "protect at all costs."

**Cut first (nice-to-have):**
- Excel/CSV attachment handling (PDF + plain text is enough for demo)
- SOP playbook editor UI (hardcode the rules, show the Firestore document in the demo)
- Learning loop (mention it as "future work" in the deck)
- WhatsApp integration (not needed for Phase 1)
- Cloud Scheduler for PO follow-ups (trigger manually for the demo)

**Cut reluctantly (improves demo quality):**
- Metrics dashboard panel (use static numbers in the deck instead)
- Multiple exception types (one exception type — price mismatch — is enough)
- Fuzzy SKU matching (exact match only, ensure demo data matches)
- Email quality gate (Person 1 manually reviews outbound emails for demo)

**Protect at all costs (demo breaks without these):**
- Email ingestion → extraction pipeline (this IS the product)
- Validation → routing decision (this IS the intelligence)
- Firestore write → dashboard display (this IS the proof it works)
- Cloud Run deployment (judges need a live URL)
- Demo video (this is what judges actually watch)

## The Tradeoffs

**Speed vs robustness.** A hackathon system that handles 5 email formats flawlessly beats one that handles 50 formats with 20% error rate. Optimize for the demo emails you control, not for arbitrary inputs you cannot predict. In production, you would need to handle every format. For the demo, you need to handle the 3-5 formats you prepared.

**Real Gmail vs simulated emails.** Using real Gmail API with real email delivery is more impressive but harder to debug. The alternative is a mock that reads from a Firestore collection and simulates email arrival. For the demo video, real Gmail is worth the effort — judges can see the actual email in a real inbox. For development and testing, use the mock to avoid rate limits and OAuth headaches.

**Solo submission vs team submission.** The companion research ([[Supply-Chain-Hackathon-Submission]]) was written for a solo builder. This plan assumes 3 people. With a team, you can build more, but coordination overhead is real. The daily 30-minute syncs on integration days are not optional — they prevent the "I built my part but it doesn't connect to yours" failure mode that kills hackathon teams.

**Phase 1 MVP vs Phase 2 vision.** Phase 1 needs a working prototype. Phase 2 (if selected for Top 100) needs visible improvement. Do not build the Phase 2 version in Phase 1. Build the minimum that demonstrates the concept, submit it, and use the Phase 2 timeline (May 29 - June 9) to add depth. Judges evaluate what is submitted, not what is planned.

**ADK `adk deploy cloud_run` vs manual Dockerfile.** The ADK CLI provides a one-command deployment (`adk deploy cloud_run --project=$PROJECT --region=$REGION --with_ui ./agents`). This is faster but less configurable. A manual Dockerfile gives more control over dependencies, multi-stage builds, and startup behavior. For a hackathon, start with `adk deploy cloud_run`. If it works, ship it. If it hits edge cases, fall back to the manual approach.

## What Most People Get Wrong

**Building bottom-up instead of demo-out.** The instinct is to build infrastructure first: set up the database, configure the message queue, deploy the CI pipeline, then start building features. This is correct for production. It is wrong for a hackathon. Start with the demo scenario. Work backward from "what does the 2-minute video show?" to "what must be working?" Build exactly that, then fill in infrastructure to support it. If you build infrastructure for 2 weeks and run out of time before the agent works, you have nothing to demo.

**Premature optimization.** Do not implement caching, rate limiting, retry logic, circuit breakers, or any resilience pattern before the happy path works. These are Week 4 buffer tasks if you have time. The demo does not test resilience. It tests functionality.

**Underestimating the demo video.** The demo video is the single most important deliverable. It is what judges watch first. A mediocre product with an excellent video beats an excellent product with a mediocre video. Allocate 2 full days for video recording and editing. Script every second. Rehearse the live demo 5 times before recording. Have a backup plan if the live system fails during recording (pre-recorded screen capture of each step, edited together to look seamless).

**Trying to be comprehensive.** Two agents (Order Intake + PO Confirmation) working end-to-end on a narrow scope is far more impressive than five agents that each do one thing and do not connect to each other. Depth beats breadth. A judge who sees an email arrive, get parsed by AI, validated against real data, and create a real order with a real confirmation email sent back will be more convinced than a judge who sees five separate agent demos that never interact.

**Ignoring the scoring criteria.** Technical Merit (40%) means multi-service Google Cloud integration, not just a Gemini API call. Use Cloud Run + Firestore + Pub/Sub + Gmail API + Gemini — that is 5 Google services in one architecture. Innovation (25%) means do something the other 500 teams are not doing — the anti-portal principle, the graduated autonomy, the learning loop. Alignment (25%) means every slide traces back to the theme language. UX (10%) means the dashboard is clean, not beautiful.

## Connections

- [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]] — The demo script this build plan executes against. The scenario defines scope.
- [[Glacis-Agent-Reverse-Engineering-Deployment]] — Cloud Run + Firebase Hosting deployment details for Week 3. Dockerfile, gcloud commands, firebase.json configuration.
- [[Glacis-Agent-Reverse-Engineering-Overview]] — The full research map. This build plan is the final note in the research hierarchy, turning 26 other notes into a shipping plan.
- [[Glacis-Agent-Reverse-Engineering-Synthetic-Data]] — Seed data generation for the demo. Person 2's Day 2-6 work depends on this note.
- [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] — Firestore collection schemas. Day 1 schema agreement is based on this note.
- [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] — Pub/Sub event contracts. Day 3 topic setup is based on this note.
- [[Supply-Chain-Hackathon-Submission]] — Phase 1 submission requirements and scoring strategy. The deliverables checklist for Day 25.

## References

### Timeline and Submission
- [Google Solution Challenge 2026 — EduLinkUp](https://edulinkup.dev/blog/google-solution-challenge-2026-your-chance-at-bagging-1000000) — Phase 1 timeline: Registration March 6 - April 15, Prototype Submission March 13 - April 24, Top 100 announced May 29, Grand Finale last week of June 2026
- [GDSC Solution Challenge Timeline — Google for Developers](https://developers.google.com/community/gdsc-solution-challenge/timeline) — Official timeline and judging criteria (Impact 25pts, Technology 25pts)

### Deployment
- [ADK Cloud Run Deployment — Google ADK Docs](https://adk.dev/deploy/cloud-run/) — `adk deploy cloud_run` command, Dockerfile, environment variables, Secret Manager configuration
- [Deploy Python FastAPI to Cloud Run — Google Cloud](https://docs.cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-fastapi-service) — `gcloud run deploy --source .` quickstart, buildpack auto-detection
- [Firebase Hosting + Cloud Run — Google Firebase](https://firebase.google.com/docs/hosting/cloud-run) — Rewrite rules for routing Hosting requests to Cloud Run services

### Hackathon Planning
- [How to Run a Successful AI Agent Hackathon — Unified AI Adoption](https://www.unifiedaiadoption.com/post/how-to-run-an-ai-agent-hackathon) — Team sizing, sprint structure, scope management for AI agent builds
- [How to Build AI Agents in 2 Days — Corporate Hackathon](https://corporate.hackathon.com/articles/how-to-build-ai-agents-in-2-days-using-a-hackathon-no-coding-required) — Idea-to-prototype in 2 days, production in 2-6 weeks

### Cost
- [Cloud Run Pricing — Google Cloud](https://cloud.google.com/run/pricing) — Free tier: 180,000 vCPU-seconds, 360,000 GiB-seconds, 2M requests/month
- [Firestore Pricing — Google Cloud](https://cloud.google.com/firestore/pricing) — Free tier: 1 GB stored, 50K reads, 20K writes, 20K deletes per day
- [Firebase Pricing — Google](https://firebase.google.com/pricing) — Hosting: 10 GB storage, 360 MB/day transfer on free tier
