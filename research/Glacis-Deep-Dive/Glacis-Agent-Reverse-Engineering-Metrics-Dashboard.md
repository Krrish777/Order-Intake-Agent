---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Metrics and Observability Dashboard"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 3
date: 2026-04-08
tags:
  - research
  - supply-chain
  - metrics
  - observability
  - dashboard
  - otif
---

# Metrics and Observability Dashboard

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]]. Depth level: 3. Parent: Both agents (Order Intake + PO Confirmation)

A supply chain AI agent without built-in metrics is a black box that processes email and produces ERP records. Nobody trusts a black box with purchase orders. The difference between a hackathon demo that impresses judges and one that gets a polite nod is showing the before/after story in real time — processing time dropping from minutes to seconds, touchless rates climbing, error rates falling, costs shrinking. Glacis's enterprise case studies are built on these numbers: Pfizer's 80% touchless rate, Carlsberg's 92%, BraunAbility's 30% OTIF improvement. Those numbers did not emerge from faith in the technology. They emerged from dashboards that proved the technology worked.

This note designs the metrics layer for the Glacis reverse-engineering build: what to measure, how to collect it, how to display it, and how to use the before/after narrative as a demo weapon.

---

## The Problem

### Why Metrics Are Not Optional

Enterprise procurement teams will not deploy an AI agent based on a PowerPoint deck. They need evidence. Glacis's whitepapers are effective because they contain specific, quantified results from named companies. Our hackathon demo needs the same rigor at a smaller scale — not "the agent processes orders faster" but "this order took 47 seconds end-to-end vs. the 11-minute manual baseline."

There are three audiences for these metrics, each with different needs:

**Judges and evaluators** want a compelling before/after story. They want to see a number change on screen during the demo. Processing time dropping from a displayed "manual baseline" to a live-measured actual time is visceral. A bar chart showing touchless rate climbing from 0% to 85% over a simulated day tells a story that a static slide cannot.

**Procurement operators** (the actual users in production) want operational visibility. Which orders are stuck? Which suppliers have not confirmed? What is the current exception backlog? How many orders processed today vs. yesterday? They do not care about token costs or latency percentiles. They care about OTIF and order backlog.

**Engineering teams** want system health. Are Gemini API calls succeeding? What is the p95 latency on extraction? Is the Pub/Sub subscription falling behind? Are Cloud Run instances scaling correctly? This is standard observability — traces, metrics, logs — applied to an AI agent pipeline.

The mistake most agent demos make is serving only one audience. A demo that shows only engineering metrics (latency, throughput) fails to connect with business judges. A demo that shows only business metrics (OTIF, cost savings) fails to demonstrate technical sophistication. The right dashboard serves all three from the same underlying data.

### The Metrics That Matter

From the Glacis whitepapers and Conexiom's OTIF research, here are the metrics that proved the business case at enterprise scale:

| Metric | Manual Baseline | Automated Target | Why It Matters |
|--------|:--------------:|:----------------:|----------------|
| Processing time per order | 8-15 minutes | <60 seconds | Most visceral demo metric — judges see it happen |
| Touchless rate | 0% (all manual) | >80% | Percentage of orders requiring zero human intervention |
| Cost per order | $10-15 | $1.77-5.00 | Direct labor savings; the CFO metric |
| Error rate | 1-4% | <0.1% | Errors cascade into OTIF failures |
| OTIF score | 84% (industry avg) | 91%+ | The ultimate supply chain KPI — on-time, in-full delivery |
| Supplier confirmation rate | ~60% within SLA | 92% within 48h | PO Confirmation agent's primary metric |
| Exception resolution time | Hours to days | Minutes | Time from exception detection to resolution |
| Orders in queue | Variable | Real-time count | Operational backlog visibility |

The OTIF metric deserves special attention. OTIF (On-Time, In-Full) measures whether an order was delivered when promised and with the correct items and quantities. Industry benchmarks from Conexiom place 98%+ as best-in-class, 90-97% as competitive, 80-89% as average, and below 80% as at-risk. BraunAbility achieved a 30% boost in supplier OTIF to 90% after deploying the PO Confirmation agent. The connection is causal: faster confirmation processing means earlier visibility into delivery date changes, which means fewer surprises at fulfillment time.

---

## First Principles

Observability for an AI agent pipeline is not fundamentally different from observability for any distributed system. You have three signal types — traces, metrics, and logs — and you need to correlate them. The difference is what you measure and what you derive from those measurements.

**Traces** follow a single order through the entire pipeline: email received, format detected, extraction started, extraction completed, validation started, validation completed, routing decision made, ERP record created. Each span in the trace carries timing data and metadata (which Gemini model was used, how many tokens, what confidence score). A single trace tells the story of one order. Aggregated traces tell the story of the system.

**Metrics** are the aggregated numbers: orders processed per hour, median processing time, touchless rate, error rate. These are what the dashboard displays. They are derived from traces and from Firestore counters that the agent updates as it works.

**Logs** are the detailed record: the full extraction output, the validation results, the routing decision rationale, the exception details. Logs are for debugging and audit, not for dashboards. They matter for [[Glacis-Agent-Reverse-Engineering-Security-Audit]] but are too verbose for real-time display.

The design principle: **instrument at the trace level, aggregate at the metric level, display at the business level**. The dashboard never shows raw spans or log lines. It shows business metrics — processing time, touchless rate, OTIF — derived from the underlying instrumentation. Engineering metrics (API latency, error rates, queue depth) live on a separate tab for the technical audience.

---

## How It Actually Works

### Collection Layer: OpenTelemetry + Firestore Counters

The collection strategy uses two complementary mechanisms:

**OpenTelemetry (OTel) traces** for timing and system health. Every agent function is instrumented with OTel spans. The extraction function creates a span with attributes for document type, page count, model used, token count, and extraction confidence. The validation function creates a span with attributes for each rule checked and its result. The routing function creates a span recording the decision (auto-execute, clarify, escalate) and the reason. Cloud Run has built-in OTel integration — traces export to Cloud Trace with zero additional infrastructure.

```python
from opentelemetry import trace
from opentelemetry.trace import StatusCode
import time

tracer = trace.get_tracer("order-intake-agent")

async def process_order(email_message: dict) -> OrderResult:
    """Full order processing pipeline with OTel instrumentation."""
    with tracer.start_as_current_span("process_order") as span:
        span.set_attribute("order.email_id", email_message["id"])
        span.set_attribute("order.customer", email_message.get("from", "unknown"))
        start = time.monotonic()

        # Step 1: Format detection
        with tracer.start_as_current_span("detect_format") as fmt_span:
            doc_type = detect_format(email_message)
            fmt_span.set_attribute("document.type", doc_type)
            fmt_span.set_attribute("document.attachment_count",
                                   len(email_message.get("attachments", [])))

        # Step 2: Extraction
        with tracer.start_as_current_span("extract_order") as ext_span:
            extraction = await extract_order(email_message, doc_type)
            ext_span.set_attribute("extraction.model", extraction.model_used)
            ext_span.set_attribute("extraction.token_count", extraction.tokens)
            ext_span.set_attribute("extraction.confidence", extraction.confidence)
            ext_span.set_attribute("extraction.line_item_count",
                                   len(extraction.line_items))

        # Step 3: Validation
        with tracer.start_as_current_span("validate_order") as val_span:
            validation = await validate_order(extraction)
            val_span.set_attribute("validation.passed", validation.all_passed)
            val_span.set_attribute("validation.warnings", len(validation.warnings))
            val_span.set_attribute("validation.failures", len(validation.failures))

        # Step 4: Routing
        with tracer.start_as_current_span("route_order") as route_span:
            result = await route_order(extraction, validation)
            route_span.set_attribute("routing.decision", result.decision)
            route_span.set_attribute("routing.reason", result.reason)

        elapsed = time.monotonic() - start
        span.set_attribute("order.processing_time_seconds", elapsed)
        span.set_attribute("order.touchless", result.decision == "auto_execute")

        # Update Firestore counters (see below)
        await update_metrics_counters(result, elapsed)

        return result
```

**Firestore counters** for business metrics that need real-time dashboard display. OTel traces are excellent for engineering observability but awkward for real-time business dashboards — querying Cloud Trace to compute "touchless rate in the last hour" requires aggregation queries that are too slow for a live dashboard. Instead, the agent updates Firestore counters after every order:

```python
from google.cloud import firestore
from datetime import date

db = firestore.AsyncClient()

async def update_metrics_counters(result: OrderResult, elapsed: float):
    """Update real-time Firestore counters after each order."""
    today = date.today().isoformat()
    metrics_ref = db.collection("metrics").document(today)

    # Atomic increment — safe for concurrent Cloud Run instances
    await metrics_ref.set({
        "orders_processed": firestore.Increment(1),
        "total_processing_seconds": firestore.Increment(elapsed),
        "touchless_count": firestore.Increment(1 if result.decision == "auto_execute" else 0),
        "clarify_count": firestore.Increment(1 if result.decision == "clarify" else 0),
        "escalate_count": firestore.Increment(1 if result.decision == "escalate" else 0),
        "error_count": firestore.Increment(1 if result.has_errors else 0),
        "total_tokens": firestore.Increment(result.tokens_used),
        "total_cost_cents": firestore.Increment(int(result.estimated_cost * 100)),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
```

The daily document pattern (`metrics/2026-04-08`) gives you natural time-series aggregation. Query the last 7 documents for a week view, the last 30 for a month view. Firestore's `Increment` operation is atomic — multiple Cloud Run instances can update the same counter simultaneously without race conditions.

For the PO Confirmation agent, add supplier-specific counters:

```python
async def update_supplier_metrics(supplier_id: str, confirmed: bool, sla_met: bool):
    """Track per-supplier confirmation performance."""
    ref = db.collection("supplier_metrics").document(supplier_id)
    await ref.set({
        "total_pos": firestore.Increment(1),
        "confirmed_count": firestore.Increment(1 if confirmed else 0),
        "sla_met_count": firestore.Increment(1 if sla_met else 0),
        "last_activity": firestore.SERVER_TIMESTAMP,
    }, merge=True)
```

### Display Layer: Firebase Hosting Real-Time Dashboard

The dashboard is a static web app on Firebase Hosting that reads Firestore counters via real-time listeners. When a counter updates, the dashboard updates instantly — no polling, no refresh.

The dashboard has three views:

**Executive View** (the demo default): Four large KPI cards across the top — Orders Processed Today, Touchless Rate (%), Avg Processing Time, Cost Savings vs Manual. Below the cards, a timeline chart showing orders processed over the last N hours with touchless/clarify/escalate breakdown. Below that, a before/after comparison panel showing manual baseline metrics side-by-side with actual automated metrics.

**Operations View**: Active exception queue (orders waiting for human review), supplier confirmation status (confirmed/pending/overdue by supplier), order pipeline (how many orders at each stage right now), and OTIF trend chart.

**Engineering View**: Gemini API latency (p50/p95/p99), token usage by model tier, extraction confidence distribution, Pub/Sub subscription lag, Cloud Run instance count, error rate by stage.

The real-time listener pattern for the executive view:

```javascript
// Firebase SDK real-time listener — dashboard updates instantly
import { doc, onSnapshot } from "firebase/firestore";

const today = new Date().toISOString().split("T")[0];
const metricsRef = doc(db, "metrics", today);

onSnapshot(metricsRef, (snapshot) => {
    const data = snapshot.data();
    if (!data) return;

    const touchlessRate = data.orders_processed > 0
        ? (data.touchless_count / data.orders_processed * 100).toFixed(1)
        : "0.0";

    const avgProcessingTime = data.orders_processed > 0
        ? (data.total_processing_seconds / data.orders_processed).toFixed(1)
        : "0.0";

    const costSavings = data.orders_processed * 10  // $10 manual baseline
        - (data.total_cost_cents / 100);             // actual AI cost

    updateKPICard("orders-processed", data.orders_processed);
    updateKPICard("touchless-rate", `${touchlessRate}%`);
    updateKPICard("avg-time", `${avgProcessingTime}s`);
    updateKPICard("cost-savings", `$${costSavings.toFixed(2)}`);
});
```

### The Before/After Narrative as Demo Weapon

The most effective demo pattern is not "look at these impressive numbers." It is "watch the numbers change." The demo scenario from [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]] should have the dashboard visible throughout. The flow:

1. **Show the baseline**: Display manual processing metrics — 11 minutes average, 0% touchless, $12.50 per order, 2.3% error rate. These are static numbers from the Glacis whitepapers, displayed as "Before AI" on the left half of the comparison panel.

2. **Process the first order**: Send an email to the demo inbox. The dashboard shows the order entering the pipeline. Extraction completes. Validation passes. Auto-executed. The processing time counter updates: "47 seconds." The touchless rate jumps to 100% (1/1). The cost counter shows "$0.03."

3. **Process a batch**: Trigger 10 pre-staged demo emails simultaneously. The dashboard shows orders flowing through the pipeline in real time. The touchless rate settles around 80% (8/10 auto-executed, 1 clarification, 1 escalation). Average processing time: 52 seconds. Cost per order: $0.04.

4. **Show the exception**: One of the 10 emails had a price mismatch. The dashboard's exception queue shows it with the mismatch highlighted. One click to approve. The escalated order's processing time was 52 seconds of AI work + however long the judge took to click approve. Still faster than 11 minutes of manual work.

5. **Close on OTIF**: Show the projected OTIF improvement. "If this manufacturer processes 1,000 orders per month and reduces error rate from 2.3% to 0.1%, that is 22 fewer wrong shipments per month. At their baseline OTIF of 84%, eliminating those errors alone pushes OTIF to 86.2%. Add the faster confirmation cycle from the PO Confirmation agent, and you reach the 91% that BraunAbility documented."

CBS Consulting's supply chain analytics cockpit demonstrates that modern AI observability can determine causes and effects of supply chain anomalies within 120 seconds. Our dashboard proves the same principle: the agent does not just process orders, it provides real-time visibility into order health that manual processing never could.

---

## The Tradeoffs

### Real-Time vs. Batch Aggregation

Real-time Firestore counters update the dashboard instantly but cost money — every counter increment is a Firestore write operation ($0.18 per 100K writes). At 1,000 orders/day with 8 counter fields per order, that is 8,000 writes/day or ~$0.014/day. Trivial. But if you add per-order-line counters, per-customer counters, per-supplier counters, and per-product counters, writes multiply fast. The pragmatic approach: real-time counters for the top-level KPIs (orders processed, touchless rate, processing time, cost), batch aggregation for everything else (per-customer trends, per-product error rates, OTIF calculations). Run a scheduled Cloud Run job every 15 minutes that queries the orders collection and updates derived metrics.

### OpenTelemetry Overhead

OTel instrumentation adds latency — typically 1-3ms per span, which is noise in a pipeline where Gemini extraction takes 2-10 seconds. The real cost is in trace export. Cloud Trace ingestion is free for the first 2.5 billion spans/month, which is more than enough. But if you export to a third-party backend (Datadog, Grafana Cloud), costs scale with span volume. For a hackathon demo, Cloud Trace is sufficient. For production, the decision is whether the engineering team already has an observability stack they prefer.

### Dashboard Complexity vs. Demo Impact

A dashboard with 20 charts and 50 metrics impresses nobody. It overwhelms. The demo dashboard should have 4 KPI cards and 2 charts, maximum. Everything else goes on secondary tabs that you never open during the demo. The temptation to show everything is strong — resist it. The judge remembers "processing time dropped from 11 minutes to 47 seconds" because it was the biggest number on screen. They do not remember the 17th chart in a scrolling grid.

### Firestore Counter Pattern vs. Time-Series Database

A purpose-built time-series database (InfluxDB, TimescaleDB, Prometheus) would be the right choice for production observability. But it is the wrong choice for a hackathon. Firestore counters with daily documents give you good-enough time-series data with zero additional infrastructure. The tradeoff: you lose sub-minute granularity (counters aggregate at the document level, not per-second) and complex queries (Firestore cannot compute percentiles or histograms natively). For a demo, daily aggregates and simple ratios are sufficient. For production, migrate the metrics pipeline to a proper time-series backend while keeping the real-time dashboard on Firestore listeners for instant updates.

---

## What Most People Get Wrong

### Treating Metrics as an Afterthought

"We will add metrics after the core agent works." This is backwards. Metrics are not a feature you bolt on — they are the evidence that the core agent works. Without metrics, the demo is "trust me, it processed the order." With metrics, the demo is "it processed the order in 47 seconds at $0.03 with 98% confidence, here is the trace." The instrumentation code should be written alongside the pipeline code, not after it.

### Showing Only Engineering Metrics

Latency percentiles and token counts impress engineers. They bore everyone else. The Google Solution Challenge judges are evaluating impact, innovation, and alignment — not whether your p95 is under 500ms. The dashboard must lead with business metrics: processing time in human-understandable units (seconds, not milliseconds), cost savings in dollars, touchless rate as a percentage. Engineering metrics are the backup — available if asked, never the lead.

### Confusing OTIF with Order Accuracy

OTIF is not the same as extraction accuracy. A 99% extraction accuracy rate can still produce poor OTIF if the remaining 1% of errors are on delivery dates or quantities that cause fulfillment failures. OTIF measures the end-to-end outcome: did the customer receive the right products, in the right quantities, at the right time? The agent improves OTIF by reducing upstream errors (better extraction, faster validation, earlier exception detection), but OTIF itself is measured at the delivery point, not at the order entry point. The dashboard should show both: extraction accuracy as a leading indicator and OTIF as the lagging outcome metric.

### Fabricating Baseline Numbers

It is tempting to inflate the manual baseline to make the improvement look dramatic. Do not do this. The Glacis whitepapers provide real, cited baselines: 8-15 minutes per order, $10-15 per order, 1-4% error rate, 84% OTIF. Use these numbers with attribution. Fabricated baselines destroy credibility the moment a judge asks "where does the 20-minute baseline come from?" Honest baselines with real sources are more impressive, not less, because they demonstrate research rigor.

### Not Logging the Manual Comparison Path

The most powerful demo feature is a side-by-side: "Here is what the CSR would do manually, and here is what the agent did." If you do not log the manual-equivalent steps (how many fields would need manual entry, how many systems would the CSR check, how long the typical manual process takes for this document type), you cannot show the comparison. Build the manual baseline as static reference data in Firestore — average times per document type, error rates per format — and display it alongside the live automated metrics.

---

## Connections

- **Parent (Order Intake)**: [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] — the pipeline this metrics layer instruments
- **Parent (PO Confirmation)**: [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] — supplier-side metrics (confirmation rate, SLA adherence)
- **Child**: [[Glacis-Agent-Reverse-Engineering-Dashboard-UI]] — the actual UI design for the Firebase Hosting dashboard (layout, components, styling)
- **Sibling**: [[Glacis-Agent-Reverse-Engineering-Security-Audit]] — audit trail logging is the security counterpart to metrics collection
- **Sibling**: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] — ERP sync status is a key operational metric
- **Infrastructure**: [[Glacis-Agent-Reverse-Engineering-Overview]] — enterprise metrics from Glacis case studies that define our targets
- **Wiki**: [[opentelemetry]] — vendor-neutral instrumentation framework
- **Wiki**: [[firestore]] — real-time database powering both counters and listeners
- **Wiki**: [[firebase]] — hosting platform for the dashboard static app
- **Wiki**: [[observability-and-tracing]] — general observability patterns applied here
- **Wiki**: [[real-time-monitoring]] — continuous monitoring principles

---

## Subtopics for Further Deep Dive

1. **OTIF Calculation Engine** — Computing OTIF from order entry through fulfillment; weighting by customer priority; trend analysis; connecting order accuracy to delivery performance; the 84% to 91% improvement path
2. **Alerting and Anomaly Detection** — Threshold-based alerts (touchless rate drops below 70%, processing time exceeds 2 minutes) vs. anomaly detection (sudden spike in escalations from one customer); integration with Cloud Monitoring
3. **Cost Attribution Model** — Breaking down per-order cost into components: Gemini tokens (by model tier), Firestore reads/writes, Cloud Run compute, Pub/Sub messages; identifying optimization opportunities from cost data
4. **A/B Testing Framework** — Comparing extraction prompt versions, model choices, validation rules by routing a percentage of traffic to the experimental path and measuring metrics differences; the metrics layer as the experiment measurement system
5. **Historical Trend Analysis** — Moving beyond daily counters to weekly/monthly trends; seasonality detection; capacity planning from processing volume data; predicting when manual intervention will be needed

---

## References

- Glacis, "How AI Automates Order Intake in Supply Chain," Dec 2025 — Processing time (8-15min to <60s), touchless rate (>80%), cost per order ($10-15 to $1.77-5), error rate (1-4% to <0.1%)
- Glacis, "AI For PO Confirmation V8," March 2026 — OTIF (84% to 91%), supplier confirmation rate (92% within 48h), IDEX and BraunAbility case studies
- [Conexiom, "OTIF: A Key Metric for Supply Chain Success"](https://conexiom.com/blog/otif-on-time-in-full-a-key-metric-for-supply-chain-success) — OTIF benchmarks (98%+ best-in-class, 90-97% competitive, 80-89% average), distributor improved OTIF from 88% to 97% through order automation alone
- [CBS Consulting, "AI-Assisted Decision Making for Stable OTIF Performance"](https://www.cbs-consulting.com/en/ai-assisted-decision-making-for-stable-otif-performance/) — Near-real-time OTIF dashboard, cause-and-effect determination within 120 seconds, AI-based alerts for threshold breaches
- [MintMCP, "AI Agent Security: The Complete Enterprise Guide for 2026"](https://www.mintmcp.com/blog/ai-agent-security) — Evidence-quality audit trails, organizations with proper audit trails 20-32 points ahead on AI maturity metrics
- OpenTelemetry Documentation — Traces, metrics, logs; Cloud Run integration; vendor-neutral instrumentation
- Firebase Documentation — Firestore real-time listeners, atomic increment operations, Firebase Hosting for static apps
- Google Cloud Trace Documentation — Free tier (2.5B spans/month), trace analysis, latency distributions
