---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Pub/Sub Event Architecture"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - pub-sub
  - event-driven
  - cloud-functions
  - architecture
---

# Pub/Sub Event Architecture

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 4. Parent: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]]
> Siblings: [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] | [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] | [[Glacis-Agent-Reverse-Engineering-ADK-Order-Intake]]

## The Problem

The [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] note identified five Pub/Sub topics and described the event flow at the architectural level. The [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] note covered the first event in the chain — how a Gmail push notification becomes a Pub/Sub message. This note fills the gap between those two: the complete event architecture that connects every component in the system. Every topic definition, every message schema, every dead-letter queue, every retry policy, every Cloud Scheduler trigger.

Event-driven architecture is the nervous system of this supply chain agent platform. The agents do not poll for work. They do not call each other directly. They react to events flowing through Pub/Sub. This decoupling is not an architectural preference — it is a survival requirement. When a supplier's confirmation email arrives at 3 AM and the PO Confirmation Agent processes it in 2 seconds, there must be zero coordination with the Order Intake Agent, zero awareness of the dashboard's current state, and zero dependency on whether the analytics pipeline is running. The event fires, the relevant subscribers react, and everything else is irrelevant. If one subscriber fails, the others do not know and do not care. The failed message goes to a dead-letter queue. A human investigates later. The system continues.

Google Cloud Pub/Sub provides at-least-once delivery by default, supports exactly-once delivery within Cloud Dataflow, handles automatic retry with exponential backoff, and scales to millions of messages per second. For a hackathon agent system processing dozens of orders per day, the scaling is irrelevant. What matters is the operational guarantee: messages are not lost. If the agent Cloud Run instance cold-starts slowly, the message waits. If the agent crashes mid-processing, the message redelivers. The event bus absorbs failure and converts it to latency — a far better outcome than data loss.

---

## Topic Inventory

The system uses eleven Pub/Sub topics, organized into three tiers by function.

### Tier 1: Signal Topics (External Events In)

These topics carry events that originate outside the agent system. They are the entry points.

| Topic | Publisher | Message Trigger |
|---|---|---|
| `email-received` | Gmail API push notification via Cloud Function | New email arrives in monitored inbox |
| `human-decision` | Dashboard backend (Cloud Run) | User approves, rejects, or modifies an exception |

### Tier 2: Processing Topics (Agent-to-Agent Coordination)

These topics carry events that the agents produce as they process work. They coordinate the pipeline stages without direct coupling.

| Topic | Publisher | Message Trigger |
|---|---|---|
| `order-intake` | Email classifier Cloud Function | Email classified as a customer order |
| `order-validated` | Order Intake Agent | Order passed all validation checks |
| `order-exception` | Order Intake Agent | Order has issues requiring human review |
| `po-sent` | ERP integration service | New PO transmitted to supplier |
| `po-followup-needed` | Cloud Scheduler (every 4 hours) | Time-based trigger to check overdue POs |
| `confirmation-received` | Email classifier Cloud Function | Email classified as a supplier confirmation |
| `confirmation-validated` | PO Confirmation Agent | Confirmation matches PO, no discrepancies |
| `confirmation-exception` | PO Confirmation Agent | Confirmation has discrepancies needing review |
| `memory-created` | Learning loop Cloud Function | New memory generated from human correction |

### Tier 3: Dead-Letter Topics

Every Tier 1 and Tier 2 topic has a corresponding dead-letter topic. Naming convention: `{original-topic}-dlq`.

| Dead-Letter Topic | Source |
|---|---|
| `email-received-dlq` | Failed email processing |
| `order-intake-dlq` | Failed order extraction or validation |
| `confirmation-received-dlq` | Failed confirmation extraction |
| `po-followup-needed-dlq` | Failed follow-up attempts |
| `human-decision-dlq` | Failed exception resolution processing |

Not every topic needs its own DLQ in the hackathon. The five listed above cover the critical paths — the places where message loss means either a customer order is dropped or a supplier confirmation is missed. The remaining processing topics (`order-validated`, `order-exception`, `confirmation-validated`, `confirmation-exception`, `memory-created`) are downstream events where failure means a dashboard does not update or an analytics record is missing. Important but not critical. These share a single `processing-dlq` catch-all topic during the hackathon, with per-topic DLQs as a production upgrade.

---

## Message Schemas

Every Pub/Sub message carries a JSON-encoded payload in `message.data` (base64-encoded by Pub/Sub) and metadata in `message.attributes`. The attributes are Pub/Sub-level metadata used for filtering and routing without deserializing the payload. The payload carries the business data.

### `email-received`

Published by the Cloud Function that handles Gmail push notifications. See [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] for the full ingestion flow.

```json
{
  "attributes": {
    "source": "gmail",
    "inbox": "orders@company.com",
    "event_type": "email-received",
    "idempotency_key": "gmail_msg_18f3a2b4c5d6e7f8"
  },
  "data": {
    "message_id": "18f3a2b4c5d6e7f8",
    "thread_id": "18f3a2b4c5d6e000",
    "from": "buyer@acme.com",
    "to": "orders@company.com",
    "subject": "PO #AC-2026-0408 - Monthly Restock",
    "received_at": "2026-04-08T10:01:23Z",
    "has_attachments": true,
    "attachment_refs": [
      {
        "filename": "Acme_PO_April.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 245000,
        "gcs_uri": "gs://project-emails/attachments/18f3a2b4c5d6e7f8/Acme_PO_April.pdf"
      }
    ],
    "body_preview": "Please find attached our April restock order. Ship to Warehouse B.",
    "classification": {
      "type": "order",
      "confidence": 0.94,
      "alternate_types": [
        { "type": "inquiry", "confidence": 0.04 },
        { "type": "confirmation", "confidence": 0.02 }
      ]
    }
  }
}
```

**The `idempotency_key`.** Gmail push notifications can fire multiple times for the same change. The Gmail History API handles deduplication at the ingestion layer, but the Pub/Sub message itself carries the Gmail message ID as an idempotency key. The subscribing agent checks: "Have I already processed `gmail_msg_18f3a2b4c5d6e7f8`?" If yes, acknowledge and skip. This is belt-and-suspenders — the ingestion layer deduplicates, and the processing layer deduplicates again. In distributed systems, you deduplicate at every boundary because you cannot trust that upstream did it correctly.

**The `classification` field.** The email classifier — a lightweight Gemini call in the ingestion Cloud Function — determines whether this email is an order, a PO confirmation, an inquiry, or noise. The classification rides in the message payload so that downstream subscribers do not need to re-classify. The `order-intake` and `confirmation-received` topics receive pre-classified messages, but the `email-received` topic carries the raw classification for audit purposes.

### `order-intake`

Published when the email classifier determines an email is a customer order. The Order Intake Agent subscribes.

```json
{
  "attributes": {
    "event_type": "order-intake",
    "customer_domain": "acme.com",
    "priority": "normal",
    "idempotency_key": "order_intake_18f3a2b4c5d6e7f8"
  },
  "data": {
    "source_email_id": "18f3a2b4c5d6e7f8",
    "customer_identifier": {
      "email": "buyer@acme.com",
      "domain": "acme.com",
      "resolved_customer_id": "CUST-001",
      "resolution_method": "domain_match",
      "resolution_confidence": 0.98
    },
    "extracted_content": {
      "body_text": "Please find attached our April restock order. Ship to Warehouse B.",
      "attachment_uris": ["gs://project-emails/attachments/18f3a2b4c5d6e7f8/Acme_PO_April.pdf"],
      "attachment_extracted_text": "PO #AC-2026-0408\nItem: Dark Roast 5lb x 50 cases\nItem: Medium Roast 5lb x 30 cases\nShip to: Warehouse B, 123 Industrial Dr, Chicago IL 60601\nRequested delivery: 2026-04-15"
    },
    "received_at": "2026-04-08T10:01:23Z"
  }
}
```

**Attachment text extraction happens before the agent.** The ingestion Cloud Function uses Document AI or a simpler OCR pipeline to extract text from PDF/image attachments and stores the result in the message. The Order Intake Agent receives structured text, not raw binary. This separation means the agent's Gemini context window is not wasted on PDF parsing instructions — it focuses on understanding the order content.

### `order-validated`

Published by the Order Intake Agent when an order passes all validation checks — item matching, price verification, inventory availability, credit check.

```json
{
  "attributes": {
    "event_type": "order-validated",
    "order_id": "ORD-20260408-001",
    "customer_id": "CUST-001",
    "auto_confirmed": "true"
  },
  "data": {
    "order_id": "ORD-20260408-001",
    "customer_ref": "CUST-001",
    "line_item_count": 2,
    "total_value": 3245.00,
    "confidence_score": 0.96,
    "all_items_matched": true,
    "match_tiers_used": [1, 1],
    "validation_summary": {
      "price_check": "pass",
      "inventory_check": "pass",
      "credit_check": "pass",
      "anomaly_check": "pass"
    },
    "firestore_doc": "orders/ORD-20260408-001",
    "validated_at": "2026-04-08T10:01:45Z"
  }
}
```

**`auto_confirmed` in attributes, not data.** Pub/Sub subscriptions can filter messages by attributes without deserializing the payload. A downstream subscription that only cares about auto-confirmed orders (e.g., an analytics pipeline tracking touchless rate) filters on `attributes.auto_confirmed == "true"` and never parses the JSON body. This is cheaper and faster. Attribute values are always strings in Pub/Sub — hence `"true"` not `true`.

### `order-exception`

Published when validation fails on any dimension.

```json
{
  "attributes": {
    "event_type": "order-exception",
    "order_id": "ORD-20260408-002",
    "severity": "critical",
    "exception_types": "credit_limit_exceeded,item_not_found"
  },
  "data": {
    "order_id": "ORD-20260408-002",
    "customer_ref": "CUST-003",
    "source_email_id": "18f3a2b4c5d6e999",
    "exceptions": [
      {
        "type": "credit_limit_exceeded",
        "severity": "critical",
        "detail": "Order total $82,500 exceeds credit limit $50,000 (utilization: $31,200)",
        "suggested_action": "Route to finance for credit increase approval"
      },
      {
        "type": "item_not_found",
        "severity": "warning",
        "detail": "Line 3: 'Espresso Deluxe 2lb' — no match above 0.7 confidence. Top candidate: SKU-9001 (0.62)",
        "suggested_action": "Present top-3 candidates to buyer for manual mapping"
      }
    ],
    "partial_validation": {
      "lines_validated": 4,
      "lines_failed": 1,
      "lines_total": 5
    },
    "firestore_doc": "orders/ORD-20260408-002",
    "created_at": "2026-04-08T10:02:10Z"
  }
}
```

**Severity in both attributes and data.** The attribute carries the highest severity across all exceptions. The data payload carries per-exception severity. The dashboard subscription filters by `attributes.severity == "critical"` to trigger immediate notifications (push notification, Slack alert). Warning-level exceptions show in the dashboard but do not interrupt anyone's workflow.

### `po-sent`

Published when a new PO is transmitted to a supplier.

```json
{
  "attributes": {
    "event_type": "po-sent",
    "po_id": "PO-20260408-001",
    "supplier_id": "SUP-042"
  },
  "data": {
    "po_id": "PO-20260408-001",
    "po_number": "PO-2026-04-042-001",
    "supplier_ref": "SUP-042",
    "supplier_email": "orders@supplier42.com",
    "line_item_count": 3,
    "total_value": 15680.00,
    "expected_response_by": "2026-04-10T10:00:00Z",
    "sla_hours": 48,
    "sent_at": "2026-04-08T10:05:00Z",
    "firestore_doc": "purchase_orders/PO-20260408-001"
  }
}
```

**`expected_response_by` is computed, not configured.** The publishing service calculates `sent_at + supplier.response_sla_hours` and includes it in the message. The PO Confirmation Agent writes this as `next_follow_up_at` in the Firestore document. Cloud Scheduler's periodic `po-followup-needed` event then queries Firestore for POs where `next_follow_up_at <= now()`. The SLA deadline flows from publisher to subscriber to persistent state to scheduler — no component needs to look up the supplier's SLA independently.

### `po-followup-needed`

Published by Cloud Scheduler on a fixed interval. This is a timer event, not a data event.

```json
{
  "attributes": {
    "event_type": "po-followup-needed",
    "trigger": "scheduled",
    "schedule_id": "po-followup-4h"
  },
  "data": {
    "check_type": "overdue_confirmations",
    "triggered_at": "2026-04-08T14:00:00Z",
    "lookback_window_hours": 168,
    "max_follow_ups": 3
  }
}
```

**The message is a query spec, not a list of POs.** The scheduler does not know which POs are overdue. It publishes a trigger that tells the PO Confirmation Agent: "Run your overdue check now. Look back 168 hours. Don't follow up more than 3 times per PO." The agent queries Firestore with these parameters and acts on the results. This keeps the scheduler stateless and the business logic in the agent.

### `confirmation-received`

Published when the email classifier identifies a supplier confirmation email.

```json
{
  "attributes": {
    "event_type": "confirmation-received",
    "supplier_domain": "supplier42.com",
    "idempotency_key": "confirmation_18f3a2b4c5d6efff"
  },
  "data": {
    "source_email_id": "18f3a2b4c5d6efff",
    "supplier_identifier": {
      "email": "confirmations@supplier42.com",
      "domain": "supplier42.com",
      "resolved_supplier_id": "SUP-042",
      "resolution_confidence": 0.99
    },
    "referenced_po": {
      "po_number_extracted": "PO-2026-04-042-001",
      "matched_po_id": "PO-20260408-001",
      "match_method": "exact_po_number"
    },
    "extracted_content": {
      "body_text": "Confirming PO-2026-04-042-001. All items confirmed. Delivery: April 14.",
      "attachment_uris": [],
      "attachment_extracted_text": null
    },
    "received_at": "2026-04-08T16:30:00Z"
  }
}
```

**PO matching happens at classification time.** The email classifier attempts to extract a PO number from the subject line or body and match it against the `purchase_orders` collection. If successful, the `matched_po_id` rides in the message. If the PO number is ambiguous or missing, `matched_po_id` is null and the PO Confirmation Agent must resolve it — a harder problem involving supplier identification and date/item heuristics.

### `confirmation-validated` and `confirmation-exception`

Follow the same pattern as `order-validated` and `order-exception`. The validated message carries the confirmation ID, the matched PO, and a summary of what was confirmed. The exception message carries discrepancy details with severity classifications.

### `human-decision`

Published when a dashboard user takes action on an exception.

```json
{
  "attributes": {
    "event_type": "human-decision",
    "entity_type": "order",
    "entity_id": "ORD-20260408-002",
    "decision": "approved_with_modifications"
  },
  "data": {
    "entity_type": "order",
    "entity_id": "ORD-20260408-002",
    "user_id": "jsmith",
    "decision": "approved_with_modifications",
    "modifications": [
      {
        "field": "line_items[2].matched_sku",
        "old_value": null,
        "new_value": "SKU-9001",
        "reason": "Customer confirmed Espresso Deluxe 2lb maps to SKU-9001"
      },
      {
        "field": "status",
        "old_value": "exception",
        "new_value": "confirmed"
      }
    ],
    "decided_at": "2026-04-08T10:15:00Z",
    "generate_memory": true
  }
}
```

**`generate_memory: true`.** When a human corrects the agent's decision, the modification is a learning signal. The `generate_memory` flag tells the downstream learning loop: "This correction is worth distilling into a memory." Not all human decisions generate memories. Approving an order without changes (the agent was right, just needed human sign-off due to threshold) does not produce a useful learning. Correcting an item mapping does. The dashboard sets this flag based on whether the human made substantive changes.

### `memory-created`

Published by the learning loop after distilling a human correction into a candidate memory.

```json
{
  "attributes": {
    "event_type": "memory-created",
    "memory_id": "MEM-20260408-001",
    "topic": "item_matching"
  },
  "data": {
    "memory_id": "MEM-20260408-001",
    "rule_text": "When Acme Corp orders 'Espresso Deluxe 2lb', map to SKU-9001 (Espresso Blend Deluxe, 2 lb Retail Bag)",
    "source_audit_ref": "audit_log/AUD-20260408-xyz",
    "source_entity": "orders/ORD-20260408-002",
    "status": "candidate",
    "created_at": "2026-04-08T10:15:30Z"
  }
}
```

This event triggers the backtest pipeline and notifies the admin dashboard that a new memory is pending review. The memory lifecycle is detailed in [[Glacis-Agent-Reverse-Engineering-Generator-Judge]].

---

## Dead-Letter Queue Configuration

Dead-letter topics (DLTs) catch messages that subscribers cannot process after repeated attempts. Without them, a poison message — one that causes a subscriber to crash every time — blocks the subscription indefinitely. Pub/Sub keeps redelivering the failed message, and new messages queue behind it. A DLT breaks this cycle.

### Configuration per Subscription

```python
from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.types import DeadLetterPolicy

subscriber = pubsub_v1.SubscriberClient()

# Example: order-intake subscription with DLT
dead_letter_policy = DeadLetterPolicy(
    dead_letter_topic="projects/scm-agents/topics/order-intake-dlq",
    max_delivery_attempts=10,
)

subscription = subscriber.create_subscription(
    request={
        "name": "projects/scm-agents/subscriptions/order-intake-agent-sub",
        "topic": "projects/scm-agents/topics/order-intake",
        "dead_letter_policy": dead_letter_policy,
        "retry_policy": {
            "minimum_backoff": {"seconds": 10},
            "maximum_backoff": {"seconds": 600},  # 10 minutes max
        },
        "ack_deadline_seconds": 120,  # 2 minutes to process
    }
)
```

**`max_delivery_attempts: 10`.** This is the number of times Pub/Sub will attempt to deliver a message before forwarding it to the DLT. Ten attempts with exponential backoff (10s to 600s) means the system retries for approximately 30-40 minutes before giving up. For a supply chain order, that is long enough to survive a Cloud Run cold start, a transient Firestore error, or a temporary Gemini API outage. It is not long enough to wait for a human to fix a bug — that is what the DLT monitoring is for.

**`ack_deadline_seconds: 120`.** The subscriber has 2 minutes to acknowledge each message. The Order Intake Agent's full pipeline — extract, match items, validate, write to Firestore — completes in 5-15 seconds for typical orders. The 120-second deadline accounts for worst-case scenarios: large orders with 50+ line items, slow Gemini API responses, or Firestore write contention. If the agent has not acknowledged within 120 seconds, Pub/Sub assumes it failed and redelivers.

### DLT Monitoring

Every DLT subscription has a Cloud Function that fires on message arrival and writes an alert to a `dlq_alerts` Firestore collection:

```typescript
// Cloud Function triggered by DLT message
interface DLQAlert {
  alert_id: string;
  original_topic: string;       // "order-intake"
  original_message_id: string;
  delivery_attempts: number;    // From CloudPubSubDeadLetterSourceDeliveryCount attribute
  original_subscription: string;
  original_publish_time: string;
  error_context: string;        // Extracted from message if available
  status: "new" | "investigating" | "resolved" | "requeued";
  created_at: Timestamp;
}
```

The dashboard displays DLQ alerts in a dedicated section. The operations team can inspect the failed message, diagnose the issue, and either fix the root cause and requeue the message to the original topic, or mark it as resolved with a note explaining why it was dropped. Messages are never silently lost.

### IAM Permissions for DLTs

Pub/Sub's service account (`service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com`) needs:
1. **Publisher role** on every dead-letter topic — to forward failed messages
2. **Subscriber role** on every source subscription — to acknowledge and remove the failed message after forwarding

Without these permissions, Pub/Sub silently fails to forward to the DLT and keeps redelivering the message indefinitely. This is the most common DLT configuration error. Grant permissions before creating subscriptions with dead-letter policies.

---

## Retry Policies

Different topics need different retry behavior based on the nature of expected failures.

| Topic | Min Backoff | Max Backoff | Max Attempts | Ack Deadline | Rationale |
|---|---|---|---|---|---|
| `email-received` | 5s | 300s | 15 | 60s | Lightweight processing; transient Gmail API errors resolve fast |
| `order-intake` | 10s | 600s | 10 | 120s | Full agent pipeline; Gemini API can have 30-60s latency spikes |
| `order-validated` | 5s | 120s | 5 | 30s | Downstream notification; if it fails 5 times, it is a bug, not a transient error |
| `order-exception` | 5s | 120s | 5 | 30s | Dashboard notification; same rationale as order-validated |
| `po-followup-needed` | 30s | 900s | 10 | 180s | Follow-up emails are time-sensitive but not real-time; longer backoff is acceptable |
| `confirmation-received` | 10s | 600s | 10 | 120s | Same pipeline complexity as order-intake |
| `human-decision` | 5s | 300s | 10 | 60s | Must not lose human decisions; more retries than downstream events |
| `memory-created` | 30s | 600s | 5 | 60s | Memory creation is background work; no urgency |

**The principle behind the numbers.** Min backoff should be long enough that a transient error has cleared but short enough that the retry happens before the user notices. Max backoff caps the worst case — you do not want a message sitting in retry limbo for an hour. Max attempts is set higher for critical-path topics (email-received, order-intake, human-decision) where message loss equals business impact, and lower for downstream topics where message loss equals a missing dashboard update.

---

## Cloud Scheduler Triggers

Two scheduled jobs drive time-based events:

### PO Follow-Up Check

```yaml
name: po-followup-check
schedule: "0 */4 * * *"        # Every 4 hours
timezone: "America/Chicago"
target:
  topic: "projects/scm-agents/topics/po-followup-needed"
  data:
    check_type: "overdue_confirmations"
    lookback_window_hours: 168  # 7 days
    max_follow_ups: 3
  attributes:
    event_type: "po-followup-needed"
    trigger: "scheduled"
```

**Why every 4 hours, not every hour.** Supplier SLAs are measured in days (24-72 hours), not hours. Checking every hour means the follow-up email sends at a time that is immaterially different from checking every 4 hours — the supplier who has not responded in 49 hours will respond or not respond regardless of whether the follow-up fires at hour 49 or hour 52. The 4-hour interval reduces Cloud Scheduler and agent invocation costs by 75% with no meaningful impact on SLA enforcement. If a customer requires tighter follow-up, the supplier's `response_sla_hours` field in Firestore drives the query, not the scheduler frequency.

### Gmail Watch Renewal

Gmail push notification subscriptions expire after 7 days. A scheduled job renews them before expiration:

```yaml
name: gmail-watch-renewal
schedule: "0 0 */6 * *"        # Every 6 days
timezone: "UTC"
target:
  topic: "projects/scm-agents/topics/system-maintenance"
  data:
    action: "renew_gmail_watch"
    inboxes: ["orders@company.com", "procurement@company.com"]
  attributes:
    event_type: "system-maintenance"
    action: "gmail-watch-renewal"
```

A Cloud Function subscribes to `system-maintenance` and calls `users.watch()` for each inbox. If the renewal fails, the dead-letter queue catches it, and the operations team is alerted before the 7-day expiration. See [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] for the watch registration details.

---

## Event Flow: End-to-End Examples

### Happy Path: Customer Order Auto-Confirmed

```
1. Customer emails order           → Gmail push → Cloud Function
2. Cloud Function classifies       → publishes to `email-received`
3. Email classifier routes         → publishes to `order-intake`
4. Order Intake Agent processes    → reads customers/, products/ from Firestore
                                   → writes to orders/ and audit_log/
                                   → publishes to `order-validated`
5. Dashboard receives onSnapshot   → updates UI in real-time
6. Cloud Function on orders/       → publishes to analytics topic → BigQuery
```

Total time: 5-15 seconds from email arrival to dashboard update.

### Exception Path: Credit Limit Exceeded

```
1-3. Same as happy path through order-intake
4. Order Intake Agent validates    → credit check fails
                                   → writes order with status "exception"
                                   → publishes to `order-exception`
5. Dashboard shows exception       → notification sent to buyer team
6. Buyer approves with modification → dashboard publishes to `human-decision`
7. Order Intake Agent re-processes → reads updated master data
                                   → writes updated order to Firestore
                                   → publishes to `order-validated`
8. Learning loop                   → generates memory from human correction
                                   → publishes to `memory-created`
```

The exception path re-enters the processing pipeline through `human-decision`, not by modifying the order directly in Firestore. This ensures the full validation pipeline runs again with the human's corrections, and the audit trail captures both the original exception and the resolution.

### Follow-Up Path: Supplier Non-Response

```
1. Cloud Scheduler fires          → publishes to `po-followup-needed`
2. PO Confirmation Agent queries  → Firestore: status == "awaiting_confirmation"
                                     AND next_follow_up_at <= now()
3. For each overdue PO:
   a. Agent composes follow-up    → sends email via Gmail API
   b. Updates PO document         → increments follow_up_count
                                   → sets next_follow_up_at
                                   → writes audit_log entry
4. If follow_up_count >= max:
   → escalates to supplier's escalation_contact
   → publishes to `confirmation-exception`
```

The follow-up email is drafted by Gemini with the supplier's name, PO number, and a professional but firm tone. The first follow-up is polite. The second is direct. The third escalates to the escalation contact. This graduated escalation mirrors how a human buyer would handle non-response — and it is exactly what Glacis describes in their PO Confirmation whitepaper.

---

## Message Ordering and Exactly-Once Semantics

### Ordering

Pub/Sub does not guarantee message order by default. For most events in this system, order does not matter — two independent customer orders can be processed in any sequence. But there is one place where ordering is critical: `human-decision` messages for the same entity. If a human approves order ORD-001 at 10:15 and then modifies it at 10:16, processing the modification before the approval produces the wrong state.

The solution: Pub/Sub ordering keys. Messages with the same ordering key are delivered to the same subscriber in publish order.

```python
publisher.publish(
    topic_path,
    data=json.dumps(payload).encode("utf-8"),
    ordering_key=f"order_{order_id}",  # Same key for all events about this order
    **attributes,
)
```

Ordering keys are used on `human-decision` and `memory-created` topics where temporal sequence matters. They are not used on `email-received` or `order-intake` because independent orders have no ordering dependency.

### Exactly-Once Processing

Pub/Sub provides at-least-once delivery. For exactly-once processing, the agent must be idempotent — processing the same message twice produces the same result. The system achieves this through two mechanisms:

1. **Idempotency keys in message attributes.** Every message carries a unique key. The agent checks Firestore for a processed-messages record with this key before processing. If found, acknowledge and skip.

2. **Firestore transactions for state changes.** The agent reads current state and writes new state in a single transaction. If a duplicate message triggers the same transaction, the read-check-write pattern detects the already-applied change and skips the write.

For the hackathon, mechanism 1 alone is sufficient. Mechanism 2 is the production hardening that prevents edge cases where the agent crashes after processing but before acknowledging, then reprocesses on redelivery.

---

## What Most People Get Wrong

**"Use one big topic for everything."** A single `agent-events` topic with a `type` attribute for routing seems simpler. It is not. Every subscriber receives every message and must filter. At hackathon scale, the wasted compute is negligible. At production scale with thousands of messages per second, you are paying for message delivery to subscribers that immediately discard 90% of what they receive. Topic-per-event-type is the standard pattern because it maps naturally to Pub/Sub's subscription model: one subscription per subscriber per event type.

**"Dead-letter queues are a nice-to-have."** They are a must-have from day one. Without a DLT, your first poison message — a malformed email that crashes the extraction pipeline — blocks all subsequent messages in that subscription. The entire order intake pipeline halts because of one bad email. With a DLT, the poison message is removed after N retries, and the pipeline continues. Five minutes to configure DLTs saves hours of incident response.

**"Cloud Scheduler can replace Pub/Sub for time-based triggers."** Cloud Scheduler publishes to Pub/Sub. It does not invoke agents directly. The scheduler is a clock, not a processor. The Pub/Sub message from the scheduler enters the same retry/DLT infrastructure as every other message. If the agent is down when the scheduler fires, the message waits. If you have the scheduler invoke a Cloud Function directly via HTTP, you get one shot — if the function is cold or errors, the schedule silently misses. Always route scheduled triggers through Pub/Sub.

**"Order does not matter in event-driven systems."** It usually does not. But when it does — human decisions on the same entity, sequential state transitions — the failure mode is subtle and destructive. An out-of-order state transition can move an order from "confirmed" back to "exception" because the exception message arrived after the confirmation message was already processed. Ordering keys on entity-scoped topics prevent this class of bug. The cost is that ordered messages go to a single subscriber (no parallel processing per ordering key), which is an acceptable tradeoff for low-throughput entity state changes.

---

## Connections

This event architecture is the nervous system that connects every component documented in the research set. The [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] note covers the Gmail-to-Pub/Sub ingestion path — the first link in the chain. The [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] note defines the data structures that agents read from and write to when processing events. The [[Glacis-Agent-Reverse-Engineering-ADK-Order-Intake]] note describes the agent code that subscribes to `order-intake` and `human-decision` topics. The [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] note provides the architectural context for why Firestore, not direct ERP calls, sits between agents and data.

The exception handling patterns — how `order-exception` and `confirmation-exception` events route to the dashboard and back through `human-decision` — are detailed in [[Glacis-Agent-Reverse-Engineering-Exception-Handling]]. The learning loop that consumes `memory-created` events is in [[Glacis-Agent-Reverse-Engineering-Generator-Judge]].

## References

### Primary Sources
- **Glacis Order Intake Whitepaper** (Dec 2025) — "under 60 seconds" processing target, shared inbox monitoring, automated follow-up cadence
- **Glacis PO Confirmation Whitepaper** (March 2026) — Graduated follow-up escalation, supplier SLA enforcement, confirmation extraction pipeline

### Web Research
- [Event-Driven Architecture with Pub/Sub — Google Cloud](https://docs.cloud.google.com/solutions/event-driven-architecture-pubsub) — Enterprise event bus patterns, fan-out, event filtering, Lambda architecture
- [Dead-Letter Topics — Google Cloud Pub/Sub](https://docs.cloud.google.com/pubsub/docs/dead-letter-topics) — DLT configuration, max delivery attempts (5-100), IAM permissions, monitoring metrics
- [Subscription Properties — Google Cloud Pub/Sub](https://docs.cloud.google.com/pubsub/docs/subscription-properties) — Retry policies, ack deadlines, ordering keys, exactly-once delivery
- [How to Set Up Retry Policies and Dead Letter Queues — OneUptime](https://oneuptime.com/blog/post/2026-02-17-how-to-set-up-retry-policies-and-dead-letter-queues-for-reliable-microservice-communication-on-pubsub/view) — Per-service DLQ patterns for microservices
- [Google Cloud Pub/Sub: Complete Guide to Real-Time Messaging — Medium](https://arindam-das.medium.com/google-cloud-pub-sub-a-complete-guide-to-real-time-messaging-bb67aca273c2) — Topic design, message schemas, delivery guarantees
