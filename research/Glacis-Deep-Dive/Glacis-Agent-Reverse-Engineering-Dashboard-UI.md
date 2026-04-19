---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Real-Time Dashboard UI Design"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - dashboard
  - firebase-hosting
  - real-time-ui
  - user-experience
---

# Real-Time Dashboard UI Design

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]]. Depth level: 4. Parent: [[Glacis-Agent-Reverse-Engineering-Metrics-Dashboard]]

The agents work in the background. The dashboard is what humans see. Every design decision in this note flows from one UX principle that Glacis embeds in their entire product philosophy: **manage by exception**. The dashboard does not show buyers and coordinators everything the agent is doing. It shows them only what needs their attention. Clean orders flow through invisibly. Exceptions surface with full context, a recommended action, and one-click resolution. The dashboard is not a monitoring tool -- it is a decision cockpit.

This note is for the internal user -- buyers, customer service coordinators, and supply chain managers. External parties (customers and suppliers) never see this dashboard. They interact via email, which is the entire point of the Anti-Portal design from [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design]].

---

## The Problem

Without a dashboard, the agent is a black box. Orders go in, things happen, and sometimes a Slack notification says "exception on PO-7890." The buyer has no way to answer basic questions: How many orders processed today? What is the touchless rate this week? Are there exceptions aging beyond SLA? Which supplier has the most unconfirmed POs? Is the system healthy?

The opposite extreme is worse: a dashboard that shows every order, every extraction, every validation check. Buyers drown in data. The whole point of the agent is to eliminate the 80% of work that does not need human attention. Surfacing that 80% in a dashboard undoes the automation.

The right answer is a dashboard with three layers: a summary that tells you if everything is fine in two seconds, an exception queue that shows exactly what needs your decision, and an audit trail that lets you investigate when something goes wrong. Most users spend 90% of their time on the summary layer, 9% on exceptions, and 1% on the audit trail.

---

## First Principles

A real-time dashboard is a state projection problem. The underlying data lives in Firestore -- orders, POs, exceptions, agent actions. The dashboard is a read-only projection of that state, updated in real time via Firestore's `onSnapshot()` listeners. The user never writes to Firestore directly through the dashboard (except for exception resolution actions, which are controlled writes through a Cloud Function).

This architecture has three properties that matter:

**Eventual consistency with fast convergence.** When the agent processes an order, it writes to Firestore. The `onSnapshot()` listener fires within 1-2 seconds on the dashboard. For a buyer watching the exception queue, an order appearing within 2 seconds of processing is effectively real-time. There is no WebSocket server to maintain, no polling interval to tune, no stale-data bug to debug. Firestore's fan-out infrastructure handles the propagation.

**Stateless frontend.** The dashboard is a static SPA deployed on Firebase Hosting. No backend server renders pages. No session state lives on a server. Every user who loads the dashboard gets the same live view, filtered by their permissions. If the dashboard container crashes (it cannot -- it is static files on a CDN), the data is still in Firestore and the agents keep running. The dashboard is disposable infrastructure.

**Listener-scoped queries.** Each dashboard panel subscribes to a different Firestore query. The overview panel listens to aggregate counters. The exception queue listens to `orders where status == 'exception' AND resolved == false`. The PO tracker listens to `purchase_orders where status in ['sent', 'awaiting', 'overdue']`. Each listener receives only the documents it cares about. This keeps network traffic proportional to the change rate of visible data, not the total data volume.

---

## Dashboard Layout: Six Panels

### Panel 1: Overview Metrics

**What it shows**: Four real-time KPI cards across the top of the dashboard. These answer "is the system healthy right now?" in under two seconds.

| Metric | Source | Update Frequency | Visual |
|--------|--------|-----------------|--------|
| Orders Today | Count of `orders` created today | Real-time (each new order) | Large number + sparkline trend (7-day) |
| Touchless Rate | `auto_executed / total_processed` today | Real-time | Percentage + color indicator (green >80%, yellow 60-80%, red <60%) |
| Avg Processing Time | Mean of `completed_at - received_at` today | Every 5 minutes (aggregated) | Time in seconds + trend arrow vs yesterday |
| Exceptions Pending | Count of unresolved exceptions | Real-time | Number + age indicator (green if all <1hr, yellow if any >1hr, red if any >4hr) |

**Firestore implementation**: The overview panel does not query individual order documents. That would mean downloading every order document to count them. Instead, the agent pipeline maintains a `daily_metrics/{date}` document with pre-aggregated counters. Each time the agent processes an order, a Cloud Function increments the relevant counter atomically using `FieldValue.increment()`. The dashboard subscribes to one document:

```javascript
import { doc, onSnapshot } from "firebase/firestore";

const today = new Date().toISOString().split("T")[0]; // "2026-04-08"

const unsubMetrics = onSnapshot(
  doc(db, "daily_metrics", today),
  (snapshot) => {
    const data = snapshot.data();
    updateKPICards({
      ordersToday: data.orders_processed,
      touchlessRate: data.auto_executed / data.orders_processed,
      avgProcessingTime: data.total_processing_ms / data.orders_processed,
      exceptionsPending: data.exceptions_open,
    });
  }
);
```

One Firestore read on load. One update per order processed. At 500 orders/day, that is 500 snapshot events -- trivial for Firestore and trivial for the browser.

**Firestore document schema** (`daily_metrics/{date}`):

```json
{
  "date": "2026-04-08",
  "orders_processed": 147,
  "auto_executed": 118,
  "human_reviewed": 22,
  "clarification_sent": 7,
  "exceptions_open": 4,
  "exceptions_resolved_today": 19,
  "total_processing_ms": 882000,
  "po_confirmations_received": 43,
  "po_confirmations_auto_matched": 38,
  "po_exceptions_open": 3,
  "updated_at": "2026-04-08T14:32:17Z"
}
```

### Panel 2: Order Queue

**What it shows**: A scrollable table of recent orders with status badges. This is the buyer's pulse check -- what came in recently, what is the status of each. Not every order needs attention, but the buyer should be able to see the flow.

| Column | Description |
|--------|-------------|
| Time | When the order was received (relative: "3 min ago") |
| Customer | Customer name from extraction |
| PO # | Customer's PO number |
| Items | Count of line items |
| Value | Order total (if prices were extracted) |
| Status | Badge: Processing / Validated / Auto-Executed / Exception / Clarification Sent |
| Confidence | Overall extraction confidence (high/medium/low with color) |

**Status badge logic**:

```
Processing (blue, animated) — agent is currently extracting/validating
Validated (green) — passed all checks, auto-executed into ERP
Exception (red) — one or more validation failures, needs human review
Clarification Sent (yellow) — agent sent a clarification email, awaiting response
On Hold (gray) — awaiting credit approval or inventory allocation
```

**Firestore query**: The order queue subscribes to the 50 most recent orders, ordered by `received_at` descending. This limits the listener's document set -- you do not need to listen to 10,000 historical orders.

```javascript
import { collection, query, orderBy, limit, onSnapshot } from "firebase/firestore";

const ordersQuery = query(
  collection(db, "orders"),
  orderBy("received_at", "desc"),
  limit(50)
);

const unsubOrders = onSnapshot(ordersQuery, (snapshot) => {
  snapshot.docChanges().forEach((change) => {
    if (change.type === "added") {
      addOrderRow(change.doc.data());
    } else if (change.type === "modified") {
      updateOrderRow(change.doc.id, change.doc.data());
    } else if (change.type === "removed") {
      // Orders don't get deleted, but handle for robustness
      removeOrderRow(change.doc.id);
    }
  });
});
```

The `docChanges()` method is the performance key. Instead of re-rendering the entire table on every update, it tells you exactly which documents were added, modified, or removed. When order #147 moves from "Processing" to "Validated," the listener fires with a single "modified" change. The UI updates one row. The other 49 rows are untouched.

**Interaction**: Clicking a row opens a detail panel showing the full extraction (all line items, addresses, special instructions), the validation results (which checks passed/failed), and the agent's recommended action. For auto-executed orders, this is read-only -- the order already went to the ERP. For exceptions, this opens the exception resolution panel.

### Panel 3: Exception Panel

**What it shows**: The heart of the dashboard. Pending exceptions with the agent's recommendation and one-click approve/reject/modify buttons. This is where the "manage by exception" philosophy becomes a UI.

Each exception card contains:

| Section | Content |
|---------|---------|
| Header | Order/PO reference, customer/supplier name, exception age (how long since flagged) |
| Exception Type | Badge: Price Mismatch / Missing Data / SKU Not Found / Credit Hold / Delivery Date / Duplicate |
| Context | The specific discrepancy: "Unit price $14.50 vs contracted $14.25 (+1.75%)" |
| Agent Recommendation | What the agent thinks should happen: "Auto-accept: within 2% tolerance" or "Request clarification: missing FedEx account" |
| Impact | Financial impact if applicable: "$50 total overage on 200 units" |
| Action Buttons | Approve Recommendation / Reject / Modify / Reassign |

**The one-click design is not a shortcut -- it is the product.** The agent has already done the analysis. It extracted the data, validated against business rules, computed the financial impact, and generated a recommendation with reasoning. The human's job is to exercise judgment on the edge case, not redo the analysis. "Approve Recommendation" means "I agree with the agent -- proceed as suggested." One click. The buyer moves to the next exception.

"Modify" opens an inline editor where the buyer can change specific fields (override the price, adjust the quantity, select a different ship-to address). The modified data goes back through the validation pipeline to confirm the changes are consistent.

"Reject" marks the exception as rejected with a reason (dropdown: "incorrect extraction," "customer will resubmit," "cancel order," "other"). Rejection reasons feed the learning loop -- if the agent's extraction was wrong, that feedback improves future extractions.

**Firestore query**: The exception panel subscribes to unresolved exceptions, ordered by age (oldest first -- SLA compliance).

```javascript
const exceptionsQuery = query(
  collection(db, "exceptions"),
  where("resolved", "==", false),
  orderBy("created_at", "asc")
);

const unsubExceptions = onSnapshot(exceptionsQuery, (snapshot) => {
  snapshot.docChanges().forEach((change) => {
    if (change.type === "added") {
      renderExceptionCard(change.doc.id, change.doc.data());
    } else if (change.type === "modified") {
      updateExceptionCard(change.doc.id, change.doc.data());
    } else if (change.type === "removed") {
      // Exception was resolved (resolved flipped to true, exits the query)
      removeExceptionCard(change.doc.id);
    }
  });
});
```

When a buyer clicks "Approve Recommendation," the dashboard calls a Cloud Function that updates the exception document (`resolved: true, resolution: "approved", resolved_by: userId, resolved_at: timestamp`), which triggers the `onSnapshot` "removed" event (the document no longer matches the `resolved == false` query), and the card disappears from the panel. The buyer sees immediate feedback without a page refresh.

**Exception resolution via Cloud Function** (not direct Firestore write):

```javascript
// Client-side: call the Cloud Function
const resolveException = httpsCallable(functions, "resolveException");
await resolveException({
  exceptionId: "exc_abc123",
  action: "approve_recommendation",
  resolvedBy: currentUser.uid,
});
```

Why a Cloud Function instead of a direct Firestore write? Security rules cannot enforce business logic complexity. The Cloud Function validates that the user has authority to resolve this exception type, logs the resolution for audit, triggers the downstream ERP update, and updates the daily metrics counters atomically. Direct writes from the client would bypass all of that.

### Panel 4: PO Tracker

**What it shows**: Purchase orders by status, with overdue alerts. This is the PO Confirmation Agent's companion view -- buyers track which POs have been sent, which are awaiting supplier confirmation, which have been confirmed, and which are overdue.

**Status pipeline**:

```
Sent → Awaiting Response → Confirmed / Has Exceptions / Overdue
```

| Column | Description |
|--------|-------------|
| PO # | Our purchase order number |
| Supplier | Supplier name |
| Sent Date | When the PO was sent |
| SLA Deadline | When we expect a response (configurable per supplier) |
| Status | Badge: Sent / Awaiting / Confirmed / Exception / Overdue |
| Items | Count of line items |
| Value | PO total value |
| Last Agent Action | "Follow-up sent Apr 6" or "Confirmation extracted Apr 7" |

**Overdue logic**: A PO is overdue when `now > sent_date + supplier_sla_days` and status is still "awaiting." The SLA is configurable per supplier in the SOP playbook ([[Glacis-Agent-Reverse-Engineering-SOP-Playbook]]). Default is 48 hours for domestic suppliers, 72 hours for international. IDEX Corporation achieved 92% supplier confirmation within 48 hours using automated reminders -- the dashboard shows you the other 8%.

**Overdue alert rendering**: Overdue POs get a red border and sort to the top of the list regardless of sent date. The age indicator shows hours overdue: "12h overdue" in yellow, "48h+ overdue" in red. Clicking an overdue PO shows the follow-up history (when the agent sent reminders, what the reminders said) and offers "Escalate to phone call" and "Re-source from backup supplier" as one-click actions.

**Firestore query**: Two listeners. One for active POs (not yet confirmed or resolved). One for a computed `overdue` flag that a Cloud Scheduler job updates every 15 minutes by scanning POs past their SLA deadline.

```javascript
const activePOsQuery = query(
  collection(db, "purchase_orders"),
  where("status", "in", ["sent", "awaiting", "overdue"]),
  orderBy("sla_deadline", "asc")
);
```

Ordering by `sla_deadline` ascending means the POs closest to their deadline (or already past it) appear at the top. The buyer sees what needs attention first.

### Panel 5: Audit Trail Viewer

**What it shows**: A searchable, chronological log of every agent action. This panel exists for three purposes: debugging when something goes wrong, compliance when an auditor asks "who approved this order and when," and learning loop analysis when you want to understand the agent's decision patterns.

Every agent action writes an audit event to Firestore. The schema:

```json
{
  "event_id": "evt_2026-04-08_abc123",
  "timestamp": "2026-04-08T14:32:17.442Z",
  "agent": "order_intake",
  "action": "extraction_completed",
  "entity_type": "order",
  "entity_id": "ord_xyz789",
  "details": {
    "source_format": "pdf_digital",
    "line_items_extracted": 3,
    "overall_confidence": "high",
    "processing_time_ms": 2340
  },
  "actor": "system",
  "correlation_id": "corr_def456"
}
```

**Key fields**:
- `correlation_id` ties together all events for a single order lifecycle. Search by correlation ID and you see the full chain: email received, classified as order, extraction completed, validation passed, auto-executed to ERP, acknowledgment email sent.
- `actor` is "system" for agent actions and a `user_id` for human actions (exception resolution, manual overrides). This is the audit trail an auditor needs: "Order #12345 was auto-executed by the system at 14:32. Exception #67890 was resolved by buyer Jane at 15:10."

**Search and filter**: The audit trail supports searching by entity ID (order number, PO number), by time range, by agent (order_intake, po_confirmation), by action type, and by actor. Full-text search is not on Firestore -- use a `correlation_id` or `entity_id` lookup for fast retrieval.

```javascript
// Search by order ID
const auditQuery = query(
  collection(db, "audit_events"),
  where("entity_id", "==", searchOrderId),
  orderBy("timestamp", "desc"),
  limit(100)
);
```

**Pagination**: Audit events accumulate fast. At 500 orders/day with ~5 events per order, that is 2,500 events/day. The audit viewer uses Firestore cursor-based pagination (`startAfter` the last visible document) rather than offset pagination. Cursor-based pagination is O(1) regardless of collection size; offset pagination degrades as the collection grows.

### Panel 6: Metrics Comparison (Before/After)

**What it shows**: Side-by-side visualization of operational metrics before and after agent deployment. This panel exists primarily for the hackathon demo -- it is the visual proof that the system delivers measurable impact. In production, it serves as the ongoing ROI dashboard for stakeholders who need to justify the investment.

| Metric | Before (Manual) | After (Agent) | Improvement |
|--------|:---------------:|:-------------:|:-----------:|
| Avg order processing time | 8-15 min | <60 sec | 93% reduction |
| Touchless rate | 0% (all manual) | 80%+ | N/A (new capability) |
| Error rate | 1-4% | <0.5% | 75%+ reduction |
| CSR time on data entry | 40-60% of day | <10% of day | 5x freed capacity |
| PO confirmation turnaround | 3-7 days | <48 hours | 60%+ reduction |
| Cost per order | $10-15 | <$1 | 90%+ reduction |

**Visualization**: Bar charts with before/after columns for each metric. Color-coded: before in gray, after in green. A horizontal line at the target threshold for each metric. The sparkline trend shows daily values for the last 30 days, so the improvement trajectory is visible -- it is not a static comparison but a live trend that shows the system getting better as thresholds are tuned.

**Data source**: The before metrics are seeded from the Glacis case study data (hardcoded baselines from the whitepaper). The after metrics come from the `daily_metrics` collection, aggregated into a `weekly_metrics` and `monthly_metrics` rollup by a Cloud Scheduler job.

For the hackathon demo, this panel tells the story in 10 seconds: "Here is what order processing looked like before. Here is what it looks like now. Here are the numbers." Judges see quantified impact without needing to understand the architecture.

---

## Tech Stack

**Firebase Hosting**: Static SPA deployed to a global CDN. Zero-config HTTPS. Automatic cache invalidation on deploy. The dashboard is a production artifact from day one -- no development server to maintain, no CORS issues, no separate deployment pipeline. `firebase deploy --only hosting` and it is live.

**Firestore**: The data layer. Real-time listeners (`onSnapshot`) push state changes to the dashboard without polling. Security rules enforce read-only access for dashboard users and write access only through Cloud Functions for exception resolution. The document structure is designed for listener-scoped queries -- each panel gets exactly the documents it needs.

**React or Vanilla JS**: The frontend framework is a secondary decision. React is the default if anyone on the team knows it -- component state maps cleanly to Firestore listener callbacks, and the reconciliation model handles frequent small updates well. Vanilla JS works if the team is more comfortable with it -- the dashboard is 6 panels, not 60 components. For a hackathon, use whatever the team ships fastest with. Do not learn a new framework during the build.

**Authentication**: Firebase Auth with Google Sign-In. Internal users authenticate with their Google Workspace accounts. The dashboard is behind authentication -- no public access. Firestore security rules check `request.auth != null` on every read. Role-based access (buyer vs manager vs admin) is enforced via custom claims on the Auth token.

```javascript
// Firestore security rule for exception resolution
match /exceptions/{exceptionId} {
  allow read: if request.auth != null;
  allow write: if false;  // No direct writes -- Cloud Functions only
}
```

---

## The Tradeoffs

**Real-time listeners vs polling.** Firestore listeners maintain a persistent connection that receives push updates. The alternative is polling (fetch data every N seconds). Listeners win on latency (1-2 second updates vs N-second polling interval), bandwidth (only changed documents are sent vs full query re-execution), and simplicity (no timer management). Listeners lose on cost at scale -- every active listener counts against Firestore's concurrent connection limits (1 million per database). For an internal dashboard with 5-50 concurrent users, this is not a concern. For a customer-facing dashboard with 10,000 users, you would need to architect differently (fan-out to a CDN, or use Firestore Bundles for read-heavy views).

**Pre-aggregated metrics vs live computation.** The overview panel reads from a pre-aggregated `daily_metrics` document instead of counting individual orders. Pre-aggregation adds complexity (the agent pipeline must update counters on every action) but eliminates the cost of scanning thousands of documents to compute a count. For a hackathon demo with 50 test orders, live computation is fine. For a production system processing 1,000+ orders/day, pre-aggregation is a requirement. The design uses pre-aggregation from the start because it is easier to build it right once than to retrofit it later.

**Cloud Functions for writes vs direct Firestore writes.** Exception resolution goes through a Cloud Function instead of direct client-side Firestore writes. This adds 100-300ms of latency to the "Approve" click. The tradeoff is worth it: the Cloud Function enforces business logic (authorization checks, cascading ERP updates, audit logging, metrics counter updates) that security rules alone cannot express. Direct writes are faster but bypass the business logic layer -- and in supply chain operations, skipping the business logic is how you get wrong orders in the ERP.

**React vs vanilla JS.** React adds a 40KB+ bundle and a learning curve. Vanilla JS adds manual DOM management complexity. For 6 panels with straightforward data binding, vanilla JS is sufficient and produces a smaller, faster-loading dashboard. React becomes worth it if the dashboard grows beyond 10 panels or if the team adds complex interactions (drag-and-drop exception prioritization, inline editing grids). For the hackathon, choose based on team skill, not theoretical scalability.

**Cursor-based vs offset pagination.** The audit trail uses cursor-based pagination (`startAfter`) instead of offset (`skip(100)`). Cursor-based pagination performs identically whether you are on page 1 or page 1,000 -- it jumps directly to the cursor position. Offset pagination reads and discards N documents to skip them, so page 1,000 is 1,000x slower than page 1. For an audit trail that accumulates thousands of events per day, cursor-based pagination is the only viable option.

---

## What Most People Get Wrong

**Showing everything.** The most common dashboard failure is displaying every order, every validation step, every agent action in a single scrolling feed. This is not a dashboard -- it is a log viewer. Buyers do not want to monitor 500 successful orders to find the 4 that need attention. The exception-first design means the default view is exceptions. If there are zero exceptions, the dashboard is calm: four green KPI cards and an empty queue. That calm state is the product working correctly.

**Polling instead of listening.** Teams accustomed to REST APIs default to polling: fetch orders every 5 seconds, re-render the table, diff against the previous state. With Firestore, this is unnecessary and wasteful. The `onSnapshot` listener gives you exactly the documents that changed, exactly when they change. The frontend code is simpler (no timer management, no diff logic) and the user experience is better (instant updates vs 5-second stale data). Firestore was designed for this pattern -- use it.

**Letting the client write directly to Firestore.** It is tempting to let the "Approve" button write `{resolved: true}` directly to the exception document. Firestore security rules can enforce that only authenticated users write. But security rules cannot enforce: "when this exception is resolved, update the ERP, decrement the exceptions_open counter, increment exceptions_resolved_today, write an audit event, and check if the resolution changes the order status." That logic belongs in a Cloud Function. The client calls the function. The function does the work atomically.

**Building the dashboard before the agent.** The dashboard is a projection of agent state. If there is no agent producing data, there is nothing to display. Build the agent pipeline first (extraction, validation, exception routing), seed it with test data, then build the dashboard on top of real Firestore documents. For the hackathon, this means the dashboard is built in the last 3-4 days of the sprint, not the first.

**Designing for external users.** The dashboard is for internal users: buyers, coordinators, managers. Suppliers and customers never see it. They interact via email -- the Anti-Portal principle. Designing the dashboard for external users would mean building authentication for external parties, handling multi-tenant data isolation, and creating a portal UX. That is the opposite of the design philosophy. Internal users get a dashboard. External users get email. If you catch yourself designing a "supplier portal view," stop and re-read [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design]].

---

## Hackathon Demo Strategy

The dashboard is the demo centerpiece. The 2-minute demo video (from [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]) needs a visual that communicates impact in seconds. The dashboard provides that visual:

1. **Opening shot** (5 seconds): Dashboard overview showing real-time KPI cards. "This is the buyer's cockpit. Four numbers tell you if the system is healthy."
2. **Live order processing** (15 seconds): Send a test email while the dashboard is visible. The order queue updates in real time -- a new row appears with "Processing" status, transitions to "Validated," then "Auto-Executed." "That order went from email to ERP in 47 seconds. No human touched it."
3. **Exception handling** (20 seconds): Send an email with a price mismatch. The exception card appears with the agent's recommendation. Click "Approve." The card disappears. "The agent flagged the discrepancy, computed the financial impact, and recommended acceptance because it is within tolerance. One click."
4. **Before/after metrics** (10 seconds): Pan to the metrics comparison panel. "8-15 minutes per order, manually. Under 60 seconds, automatically. 80% touchless. That is $14 per order saved."

The dashboard is designed to make this demo flow natural. The real-time updates are not a gimmick -- they are the proof that the system works end-to-end. The judge sees data flowing from email to dashboard to ERP in real time, with no cuts or staging.

---

## Connections

- **Parent**: [[Glacis-Agent-Reverse-Engineering-Metrics-Dashboard]] -- the metrics and observability architecture that feeds data into this dashboard.
- **Exception handling**: [[Glacis-Agent-Reverse-Engineering-Exception-Handling]] -- the 3-level autonomy system (auto-execute, clarify, escalate) that determines what appears in the exception panel. The dashboard is the escalation destination for Level 3.
- **ERP integration**: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] -- the downstream system that the dashboard's "Approve" action triggers. Exception resolution flows from dashboard click to Cloud Function to Firestore/ERP update.
- **Anti-Portal design**: [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design]] -- the foundational constraint that this dashboard is internal-only. External parties interact via email, not a portal.
- **Overview**: [[Glacis-Agent-Reverse-Engineering-Overview]] -- the full research map. This dashboard is at Level 4 (Build-Level Detail), directly above the demo scenario at Level 5.

---

## References

- [Get Realtime Updates with Cloud Firestore](https://firebase.google.com/docs/firestore/query-data/listen) -- onSnapshot(), docChanges(), detaching listeners, platform implementations
- [Understand Real-time Queries at Scale](https://firebase.google.com/docs/firestore/real-time_queries_at_scale) -- Fan-out architecture, changelog propagation, concurrent connection limits
- [Firebase Hosting Documentation](https://firebase.google.com/docs/hosting) -- Static SPA deployment, CDN, HTTPS, deploy configuration
- [Build Presence in Cloud Firestore](https://firebase.google.com/docs/firestore/solutions/presence) -- Online/offline user tracking for collaborative dashboard views
- [Building a Mobility Dashboard with Cloud Run and Firestore](https://cloud.google.com/blog/topics/manufacturing/building-a-mobility-dashboard-with-cloud-run-and-firestore) -- Real-time operational dashboard patterns with Google Cloud
- Glacis, "How AI Automates Order Intake in Supply Chain" (Dec 2025) -- 8-15 min to <60 sec processing time, 93% reduction, dashboard as human review interface
- Glacis, "AI For PO Confirmation V8" (March 2026) -- "Manage by exception" philosophy, one-click approval, buyer dashboard design principles
- IDEX Corporation case study -- 92% supplier confirmation within 48 hours, automated reminder escalation
- BraunAbility case study -- 94% PO acknowledgment rate, 30% OTIF improvement with dashboard visibility
