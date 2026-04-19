---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Firestore Data Model and Schemas"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - firestore
  - data-model
  - schema-design
  - nosql
---

# Firestore Data Model and Schemas

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 4. Parent: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]]
> Siblings: [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] | [[Glacis-Agent-Reverse-Engineering-Security-Audit]] | [[Glacis-Agent-Reverse-Engineering-Item-Matching]]

## The Problem

The [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] note sketched eight Firestore collections and their top-level fields. That was the architectural blueprint — what collections exist and why. This note is the engineering specification — the exact document schemas, composite indexes, subcollection decisions, security rules, and the reasoning behind every structural choice. If the ERP Integration note tells you what rooms the building has, this note gives you the construction drawings.

Firestore is schemaless. Every document in a collection can have different fields. This freedom is a trap. Without explicit schema definitions enforced in application code, your data model degrades within weeks — one agent writes `customer_id`, another writes `customerId`, a dashboard query expects `customer_ref`, and you spend your debugging time on field name mismatches instead of business logic. The discipline of defining schemas up front — even in a schemaless database — is the difference between a system that works and a system that works until it doesn't.

The dual role compounds the problem. Firestore serves as both the AI agents' data store AND the simulated ERP for the hackathon. That means the schemas must satisfy two very different access patterns: the agents need fast, selective reads during validation (give me this customer's credit limit and this product's price tier, nothing else), while the dashboard needs broad, real-time queries (give me all orders in exception status, sorted by timestamp). A schema that optimizes for one pattern at the expense of the other creates either slow agents or a laggy dashboard. Both are unacceptable.

---

## The Nine Collections

### 1. `customers`

Represents the customer master — the entities who send orders via email. In a real ERP, this is SAP's KNA1/KNB1 tables or Oracle's HZ_PARTIES. Here, it is the source of truth for credit limits, contract pricing, shipping addresses, and the aliases the agent uses to identify which customer sent an email.

```typescript
// Firestore path: customers/{customer_id}
interface Customer {
  customer_id: string;          // Primary key, matches ERP customer number
  name: string;                 // Legal entity name: "Acme Corp"
  account_id: string;           // Internal account reference (may differ from customer_id)
  email_domains: string[];      // ["acme.com", "acmeprocurement.com"] — for sender identification
  aliases: string[];            // ["Acme", "ACME Corp", "Acme Corporation"] — fuzzy matching
  credit_limit: number;         // In USD, e.g., 500000
  credit_used: number;          // Current outstanding balance
  default_carrier: string;      // "FedEx Ground" — used when order doesn't specify
  contract_prices: {            // SKU → negotiated price (overrides product.price_tiers)
    [sku: string]: number;
  };
  addresses: {
    billing: Address;
    shipping: Address[];        // Multiple ship-to locations
    default_shipping_index: number;
  };
  ordering_patterns: {          // Agent uses this for anomaly detection
    avg_monthly_orders: number;
    avg_order_value: number;
    typical_skus: string[];     // Most frequently ordered products
    usual_quantities: { [sku: string]: number };  // Expected qty per SKU
  };
  status: "active" | "on_hold" | "suspended";
  created_at: Timestamp;
  updated_at: Timestamp;
}

interface Address {
  label: string;                // "Main Warehouse", "Regional DC East"
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
}
```

**Why flat, not subcollections.** Customer addresses could be a subcollection (`customers/{id}/addresses/{addr_id}`), but the Order Intake Agent needs the full customer record — including all addresses — in a single read during validation. Subcollections require separate queries. Embedding addresses as an array inside the document means one read gets everything. The tradeoff: if a customer has 50 shipping addresses, the document grows. At ~200 bytes per address, 50 addresses adds 10 KB. Well within Firestore's 1 MB document limit and the practical 20 KB guideline. If a customer somehow has 500 addresses, you have a different problem — and probably a subcollection.

**The `aliases` field.** When the agent classifies an incoming email, it needs to map the sender's domain or freeform text references to a customer record. "Acme" in the email body might mean `customer_id: "CUST-001"`. The aliases array is the Tier 1 lookup table for customer identification, checked before any embedding-based matching. See [[Glacis-Agent-Reverse-Engineering-Item-Matching]] for the three-tier cascade — the same pattern applies to customer resolution.

**The `ordering_patterns` field.** This is the anomaly detection baseline. If Acme typically orders 100 cases of SKU-7042 per month and an email arrives requesting 10,000 cases, the agent flags it as anomalous. The patterns are updated monthly by a Cloud Function that aggregates completed orders. Not real-time — deliberately lagged to avoid being influenced by the very anomaly it is detecting.

### 2. `products`

The product master. Every SKU the agents can match against, with the embedding vector that powers [[Glacis-Agent-Reverse-Engineering-Item-Matching]].

```typescript
// Firestore path: products/{sku}
interface Product {
  sku: string;                  // Primary key: "SKU-7042-DR5"
  name: string;                 // "Arabica Blend Dark Roast Ground Coffee"
  description: string;          // Full product description for embedding context
  aliases: string[];            // Customer shorthand: ["Dark Roast 5lb", "DR5", "DR Ground 5#"]
  embedding: number[];          // 768-dim vector from text-embedding-004
  category: string;             // "Coffee - Ground - Dark Roast"
  unit_of_measure: string;      // "case", "each", "pallet"
  case_pack_size: number;       // Units per case: 12
  price_tiers: PriceTier[];     // Volume-based pricing
  min_order_qty: number;        // Minimum order quantity: 1
  lead_time_days: number;       // Standard lead time: 5
  status: "active" | "discontinued" | "seasonal";
  weight_lbs: number;           // For shipping calculations
  updated_at: Timestamp;
}

interface PriceTier {
  min_qty: number;              // 1, 100, 500
  max_qty: number | null;       // 99, 499, null (uncapped)
  unit_price: number;           // 24.99, 22.50, 19.99
}
```

**The embedding field.** Firestore's native vector search (GA since late 2024) supports vectors up to 2048 dimensions. Google's `text-embedding-004` produces 768-dimensional vectors. The embedding is computed at product ingest time — not query time — and stored directly in the document. When the Order Intake Agent receives a customer's line item description, it embeds the query string with the same model and uses Firestore's `findNearest()` to retrieve the top-K candidates. The vector index is defined separately (see Indexes section below).

**`price_tiers` as an array, not a map.** You might think `{ "1-99": 24.99, "100-499": 22.50 }` is cleaner. It is not. You cannot query Firestore maps by range. With an array of `PriceTier` objects, the application code iterates the tiers and finds the applicable price for a given quantity. The array is ordered by `min_qty` ascending, so the lookup is a simple linear scan — and with at most 5-6 tiers per product, performance is irrelevant.

### 3. `orders`

The transactional output of the Order Intake Agent. Each document represents a single customer order, from draft through completion.

```typescript
// Firestore path: orders/{order_id}
interface Order {
  order_id: string;             // Auto-generated: "ORD-20260408-001"
  customer_ref: string;         // Reference to customers/{customer_id}
  customer_po_number: string;   // Customer's own PO number from the email
  source_email_id: string;      // Gmail message ID for traceability
  line_items: OrderLineItem[];
  status: "draft" | "validated" | "confirmed" | "shipped" | "exception" | "cancelled";
  exception_reasons: string[];  // ["credit_limit_exceeded", "item_not_found"]
  confidence_score: number;     // 0.0-1.0, agent's overall confidence
  ship_to: Address;
  requested_delivery_date: string | null;  // ISO date, if customer specified
  created_by: "order_intake_agent" | "manual_entry";
  created_at: Timestamp;
  updated_at: Timestamp;
  approved_by: string | null;   // User ID if human-approved
  approved_at: Timestamp | null;
  audit_trail: AuditEntry[];    // Embedded mini-trail for quick dashboard display
  erp_sync: "pending" | "synced" | "failed" | "not_applicable";
}

interface OrderLineItem {
  line_number: number;
  customer_description: string; // Raw text from customer: "Dark Roast 5lb x 50"
  matched_sku: string | null;   // "SKU-7042-DR5" or null if unresolved
  match_confidence: number;     // 0.0-1.0
  match_tier: 1 | 2 | 3;       // Which matching tier resolved it
  quantity: number;
  unit_price: number;           // Price applied (from contract or tier)
  line_total: number;           // quantity * unit_price
  validation_flags: string[];   // ["price_mismatch", "qty_anomaly"]
}

interface AuditEntry {
  timestamp: Timestamp;
  action: string;               // "created", "validated", "approved", "exception_resolved"
  actor: string;                 // "order_intake_agent" or user ID
  detail: string;               // Human-readable description
}
```

**Why `audit_trail` is embedded AND in a separate collection.** The order document carries a lightweight embedded audit trail — 3-5 entries max — so the dashboard can display "Created by agent at 10:01, validated at 10:02, approved by jsmith at 10:15" without a second read. The full audit trail in `audit_log/` is the compliance-grade record with complete input/output data. The embedded trail is a denormalized convenience. When the embedded array exceeds 10 entries (unusual, but possible for heavily-edited orders), the application stops appending and adds a single "see audit_log for full history" entry.

**`match_tier` on line items.** This field exists for analytics. After a month of operation, you query: "What percentage of line items are resolved by exact match (tier 1) vs. embedding (tier 2) vs. human escalation (tier 3)?" If tier 3 is above 20%, your alias table and embeddings need enrichment. If tier 1 is above 80%, the system is learning effectively. This metric directly measures the [[Glacis-Agent-Reverse-Engineering-Item-Matching]] cascade's health.

### 4. `purchase_orders`

The PO Confirmation Agent's primary working collection. Each document represents a PO sent to a supplier, tracked through the confirmation lifecycle.

```typescript
// Firestore path: purchase_orders/{po_id}
interface PurchaseOrder {
  po_id: string;                // "PO-20260408-001"
  po_number: string;            // Official PO number sent to supplier
  supplier_ref: string;         // Reference to suppliers/{supplier_id}
  line_items: POLineItem[];
  status: "draft" | "sent" | "awaiting_confirmation" | "confirmed" | "exception" | "closed";
  original_dates: {             // What was originally requested
    order_date: string;         // ISO date
    requested_delivery: string;
  };
  confirmed_dates: {            // What the supplier confirmed (null until confirmed)
    confirmation_date: string | null;
    confirmed_delivery: string | null;
  };
  follow_up_count: number;      // Number of automated follow-ups sent
  last_follow_up_at: Timestamp | null;
  next_follow_up_at: Timestamp | null;  // Computed: last_follow_up + SLA window
  source_email_id: string | null;  // If PO was triggered by an order email chain
  confirmation_id: string | null;  // Reference to confirmations/{id} once confirmed
  created_at: Timestamp;
  updated_at: Timestamp;
}

interface POLineItem {
  line_number: number;
  sku: string;
  description: string;
  quantity: number;
  unit_price: number;
  requested_delivery_date: string;
  confirmed_quantity: number | null;
  confirmed_price: number | null;
  confirmed_delivery_date: string | null;
  discrepancy_flags: string[];  // ["qty_short", "price_increase", "date_delay"]
}
```

**The `next_follow_up_at` field.** The PO Confirmation Agent does not poll Firestore asking "which POs need follow-up?" Instead, Cloud Scheduler publishes to the `po-followup-needed` Pub/Sub topic every 4 hours. The Cloud Function that handles this event queries `purchase_orders` where `status == "awaiting_confirmation"` and `next_follow_up_at <= now()`. The `next_follow_up_at` field turns a vague "check SLA" into a precise indexed query. See [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] for the full follow-up flow.

### 5. `suppliers`

The supplier master. The PO Confirmation Agent reads this to know SLA expectations, escalation contacts, and historical reliability.

```typescript
// Firestore path: suppliers/{supplier_id}
interface Supplier {
  supplier_id: string;
  name: string;
  aliases: string[];            // ["Acme Supply", "Acme Supplies Inc", "ACME-SUP"]
  contact_email: string;        // Primary order contact
  escalation_contact: {
    name: string;
    email: string;
    phone: string | null;
  };
  response_sla_hours: number;   // Expected response time: 48
  historical_otif: number;      // On-time-in-full rate: 0.94
  confirmation_rate: number;    // Rate of POs confirmed without discrepancy: 0.87
  avg_response_hours: number;   // Actual average response time: 36
  preferred_format: "email" | "portal" | "edi";  // Almost always "email"
  status: "active" | "on_hold" | "inactive";
  updated_at: Timestamp;
}
```

**`historical_otif` and `confirmation_rate`.** These are not vanity metrics. The PO Confirmation Agent uses them to calibrate its follow-up urgency. A supplier with 0.94 OTIF and 0.87 confirmation rate gets a standard follow-up cadence. A supplier with 0.60 OTIF gets flagged proactively before SLA expiration. The values are recomputed weekly by a Cloud Function that aggregates closed POs.

### 6. `confirmations`

Each document captures a supplier's response to a PO, including the raw extracted data and any discrepancies found.

```typescript
// Firestore path: confirmations/{confirmation_id}
interface Confirmation {
  confirmation_id: string;
  po_ref: string;               // Reference to purchase_orders/{po_id}
  supplier_ref: string;
  source_email_id: string;      // Gmail message ID of supplier's response
  supplier_response_raw: string; // Cleaned text extracted from email/attachment
  extracted_data: {
    confirmed_items: ConfirmedItem[];
    delivery_date: string | null;
    notes: string | null;       // Supplier's freeform notes
  };
  discrepancies: Discrepancy[];
  resolution_status: "auto_accepted" | "pending_review" | "escalated" | "resolved" | "rejected";
  resolved_by: string | null;
  resolved_at: Timestamp | null;
  confidence_score: number;
  created_at: Timestamp;
}

interface ConfirmedItem {
  sku: string;
  confirmed_qty: number;
  confirmed_price: number;
  confirmed_delivery_date: string;
}

interface Discrepancy {
  field: "quantity" | "price" | "delivery_date" | "sku";
  line_number: number;
  expected: string | number;
  actual: string | number;
  severity: "critical" | "warning" | "info";
  auto_resolvable: boolean;     // Agent can handle without human
}
```

**Severity classification.** Not all discrepancies are equal. A supplier confirming 99 units instead of 100 is a warning. A supplier confirming a 30% price increase is critical. A delivery date 1 day late is info; 2 weeks late is critical. The severity levels drive the agent's routing decision: info and warning with `auto_resolvable: true` are accepted automatically, critical discrepancies always escalate. This maps to the autonomy tiers described in [[Glacis-Agent-Reverse-Engineering-Exception-Handling]].

### 7. `memories`

The learning loop's persistence layer. When a human corrects an agent decision, the correction is distilled into a rule and stored here for retrieval during future processing.

```typescript
// Firestore path: memories/{memory_id}
interface Memory {
  memory_id: string;
  rule_text: string;            // Natural language: "Acme Corp always rounds to nearest case pack"
  embedding: number[];          // 768-dim vector for semantic retrieval
  metadata: {
    topic: string;              // "order_validation", "item_matching", "po_confirmation"
    customer: string | null;    // Scoped to specific customer, or null for global
    supplier: string | null;
    location: string | null;
    sku: string | null;
  };
  source: {
    correction_type: string;    // "item_match_override", "qty_adjustment", "price_approval"
    original_decision: string;
    corrected_decision: string;
    audit_log_ref: string;      // Link to the audit entry that spawned this memory
  };
  status: "candidate" | "backtested" | "active" | "deprecated";
  backtest_results: {
    total_tested: number;
    correct: number;
    incorrect: number;
    accuracy: number;           // correct / total_tested
  } | null;
  created_at: Timestamp;
  activated_at: Timestamp | null;
  deprecated_at: Timestamp | null;
}
```

**The status lifecycle.** A memory starts as `candidate` — the agent proposed a rule from a human correction. It moves to `backtested` after being tested against historical data (did this rule produce correct results for past orders?). If backtest accuracy exceeds a threshold (configurable, default 0.85), it moves to `active` and the agents include it in their retrieval context. If conditions change and the rule produces errors, it moves to `deprecated`. This lifecycle prevents the system from learning the wrong lessons. See [[Glacis-Agent-Reverse-Engineering-Generator-Judge]] for the generator-judge pattern that manages this flow.

### 8. `business_rules`

Structured rules that encode business logic as data rather than code. Where `memories` stores learned heuristics in natural language, `business_rules` stores deterministic conditions.

```typescript
// Firestore path: business_rules/{rule_id}
interface BusinessRule {
  rule_id: string;
  name: string;                 // "Credit hold check"
  description: string;
  trigger_condition: {
    event: string;              // "order_validated", "po_confirmation_received"
    field: string;              // "order.total_value"
    operator: ">" | "<" | "==" | "in" | "not_in" | "contains";
    value: any;                 // 50000
  };
  action: {
    type: "block" | "warn" | "escalate" | "auto_approve" | "modify";
    detail: string;             // "Route to finance team for approval"
    target_field: string | null; // Field to modify, if action is "modify"
    target_value: any;          // New value, if action is "modify"
  };
  autonomy_level: 1 | 2 | 3;   // 1=auto-execute, 2=execute-with-notification, 3=human-approval-required
  escalation_path: string[];    // ["buyer_team", "procurement_manager", "vp_supply_chain"]
  priority: number;             // Lower = higher priority; evaluated in order
  enabled: boolean;
  created_by: string;
  created_at: Timestamp;
  updated_at: Timestamp;
}
```

**Why separate from memories.** Memories are probabilistic — "this usually works." Business rules are deterministic — "orders over $50K always require finance approval." Mixing them creates confusion about whether a rule is a suggestion or a mandate. The agent's decision pipeline evaluates business rules first (hard constraints), then retrieves relevant memories (soft guidance). A business rule can block an order that memories would approve, but memories cannot override a business rule.

### 9. `audit_log`

The compliance backbone. Append-only, immutable, complete.

```typescript
// Firestore path: audit_log/{log_id}
interface AuditLog {
  log_id: string;               // Auto-generated UUID
  timestamp: Timestamp;
  agent_id: string;             // "order_intake_agent" | "po_confirmation_agent"
  action: string;               // "order_created", "validation_passed", "exception_raised",
                                // "confirmation_extracted", "follow_up_sent", "memory_created"
  entity_type: "order" | "purchase_order" | "confirmation" | "memory" | "business_rule";
  entity_id: string;            // Document ID of the affected entity
  input_summary: {
    source_email_id: string | null;
    extracted_fields: Record<string, any>;  // What the agent saw
    context_used: string[];     // Memory IDs and business rule IDs consulted
  };
  output_summary: {
    decision: string;           // "auto_confirmed", "escalated", "exception_raised"
    confidence: number;
    result: Record<string, any>; // What the agent did
    downstream_events: string[]; // Pub/Sub topics published to
  };
  approval_status: "auto_executed" | "pending_approval" | "human_approved" | "human_rejected" | "human_modified";
  user_id: string | null;       // Non-null for human actions
  session_id: string;           // Groups related audit entries in a single processing run
}
```

**`session_id` for traceability.** When the Order Intake Agent processes a single email, it may create 5-10 audit entries: one for email classification, one per line item validation, one for the overall order decision. The `session_id` groups them so a compliance query can reconstruct the full decision chain for a single processing event. Without it, you are correlating by timestamp proximity, which is fragile when multiple emails process concurrently.

---

## Subcollection Decisions

Firestore forces a choice on every relationship: embed in the parent document, or break out into a subcollection. The decision criteria are access pattern and growth rate.

| Relationship | Decision | Reasoning |
|---|---|---|
| Customer addresses | **Embedded array** | Always read with customer; max ~50 items; no independent querying needed |
| Order line items | **Embedded array** | Always read with order; typically 5-30 items; no cross-order line item queries |
| PO line items | **Embedded array** | Same reasoning as order line items |
| Confirmation discrepancies | **Embedded array** | Always read with confirmation; max ~20 items per confirmation |
| Audit entries on orders | **Embedded array** (capped at 10) | Dashboard convenience; full trail in `audit_log/` collection |
| Memories per customer | **Root collection with metadata filter** | Need cross-customer retrieval for global memories; embedding search is collection-level |
| Business rules | **Root collection** | Queried independently by trigger_condition.event; no parent entity |
| Audit log entries | **Root collection** | Must be queryable by entity_type, entity_id, timestamp, agent_id independently |

The principle: if the child data is always accessed through the parent and has a bounded growth rate, embed it. If the child data needs independent querying, cross-parent aggregation, or unbounded growth, make it a root collection.

---

## Composite Indexes

Firestore requires composite indexes for any query that filters on multiple fields or combines a filter with an order-by on a different field. Single-field indexes are created automatically. These are the composite indexes the system needs:

```
// firestore.indexes.json
{
  "indexes": [
    // Orders: dashboard query — status filter + time sort
    {
      "collectionGroup": "orders",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "status", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    // Orders: customer-specific order history
    {
      "collectionGroup": "orders",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "customer_ref", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    // Purchase orders: overdue follow-up query
    {
      "collectionGroup": "purchase_orders",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "status", "order": "ASCENDING" },
        { "fieldPath": "next_follow_up_at", "order": "ASCENDING" }
      ]
    },
    // Purchase orders: supplier-specific PO history
    {
      "collectionGroup": "purchase_orders",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "supplier_ref", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    // Confirmations: PO lookup + time sort
    {
      "collectionGroup": "confirmations",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "po_ref", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    // Confirmations: resolution status filter for dashboard
    {
      "collectionGroup": "confirmations",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "resolution_status", "order": "ASCENDING" },
        { "fieldPath": "created_at", "order": "DESCENDING" }
      ]
    },
    // Audit log: entity history (most common compliance query)
    {
      "collectionGroup": "audit_log",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "entity_type", "order": "ASCENDING" },
        { "fieldPath": "entity_id", "order": "ASCENDING" },
        { "fieldPath": "timestamp", "order": "DESCENDING" }
      ]
    },
    // Audit log: agent activity timeline
    {
      "collectionGroup": "audit_log",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "agent_id", "order": "ASCENDING" },
        { "fieldPath": "timestamp", "order": "DESCENDING" }
      ]
    },
    // Memories: active memories by topic for agent retrieval
    {
      "collectionGroup": "memories",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "status", "order": "ASCENDING" },
        { "fieldPath": "metadata.topic", "order": "ASCENDING" }
      ]
    },
    // Business rules: active rules by trigger event
    {
      "collectionGroup": "business_rules",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "enabled", "order": "ASCENDING" },
        { "fieldPath": "trigger_condition.event", "order": "ASCENDING" },
        { "fieldPath": "priority", "order": "ASCENDING" }
      ]
    }
  ],
  "fieldOverrides": [
    // Vector index for product embedding search
    {
      "collectionGroup": "products",
      "fieldPath": "embedding",
      "indexes": [
        {
          "queryScope": "COLLECTION",
          "order": "VECTOR",
          "vectorConfig": {
            "dimension": 768,
            "distanceMeasure": "COSINE"
          }
        }
      ]
    },
    // Vector index for memory semantic retrieval
    {
      "collectionGroup": "memories",
      "fieldPath": "embedding",
      "indexes": [
        {
          "queryScope": "COLLECTION",
          "order": "VECTOR",
          "vectorConfig": {
            "dimension": 768,
            "distanceMeasure": "COSINE"
          }
        }
      ]
    }
  ]
}
```

Ten composite indexes and two vector indexes. Every one maps to a real query. The most critical is the `audit_log` entity history index — this is the query that fires when someone asks "show me everything that happened to order ORD-20260408-001." Without the composite index, Firestore would reject the query (you cannot filter on `entity_type` + `entity_id` and sort by `timestamp` without it). The vector indexes on `products` and `memories` enable the `findNearest()` calls that power item matching and memory retrieval, respectively.

**Index management in practice.** Deploy indexes with the Firebase CLI: `firebase deploy --only firestore:indexes`. Index builds take minutes to hours depending on collection size. For the hackathon demo with hundreds of documents, builds complete in under a minute. In production with millions of documents, plan index builds during off-peak hours and monitor with `gcloud firestore operations list`.

---

## Security Rules

Firestore security rules are the last line of defense. The agents run as service accounts with admin SDK access, bypassing rules entirely. Rules protect against the other attack surface: the dashboard, which uses client-side Firebase SDK with user authentication.

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Helper: check if user is authenticated
    function isAuth() {
      return request.auth != null;
    }

    // Helper: check if user has a specific role claim
    function hasRole(role) {
      return isAuth() && request.auth.token.role == role;
    }

    // Master data: read-only for dashboard users
    match /customers/{customerId} {
      allow read: if isAuth();
      allow write: if hasRole("admin");
    }

    match /products/{sku} {
      allow read: if isAuth();
      allow write: if hasRole("admin");
    }

    match /suppliers/{supplierId} {
      allow read: if isAuth();
      allow write: if hasRole("admin");
    }

    // Transactional data: read for all, write restricted
    match /orders/{orderId} {
      allow read: if isAuth();
      allow update: if isAuth()
        && request.resource.data.status in ["confirmed", "cancelled"]
        && resource.data.status == "exception";
      // Dashboard users can only confirm or cancel orders in exception status
      allow create, delete: if false;
      // Only agents create orders; nobody deletes
    }

    match /purchase_orders/{poId} {
      allow read: if isAuth();
      allow update: if isAuth()
        && request.resource.data.diff(resource.data).affectedKeys()
            .hasOnly(["status", "updated_at"]);
      // Dashboard users can only update status
      allow create, delete: if false;
    }

    match /confirmations/{confirmId} {
      allow read: if isAuth();
      allow update: if isAuth()
        && request.resource.data.diff(resource.data).affectedKeys()
            .hasOnly(["resolution_status", "resolved_by", "resolved_at"]);
      allow create, delete: if false;
    }

    // Audit log: read-only, append via admin SDK only
    match /audit_log/{logId} {
      allow read: if isAuth();
      allow create, update, delete: if false;
      // Immutable. Only service accounts (admin SDK, bypasses rules) can write.
    }

    // Memories and business rules: admin-only write
    match /memories/{memoryId} {
      allow read: if isAuth();
      allow write: if hasRole("admin");
    }

    match /business_rules/{ruleId} {
      allow read: if isAuth();
      allow write: if hasRole("admin");
    }
  }
}
```

The critical rule is on `audit_log`: `allow create, update, delete: if false`. Client-side code cannot touch the audit log. Period. Only the agents, running with admin SDK credentials that bypass rules, can append entries. This guarantees the audit trail's integrity against any client-side tampering. See [[Glacis-Agent-Reverse-Engineering-Security-Audit]] for the broader security architecture including IAM, encryption, and GDPR compliance.

The `orders` update rule is worth examining. Dashboard users can update an order, but only if: (a) they are authenticated, (b) they are changing the status to `confirmed` or `cancelled`, and (c) the current status is `exception`. This means you cannot modify a validated order from the dashboard — only resolve exceptions. The agent's workflow and the human's workflow are separated by rules, not just UI constraints.

---

## What Most People Get Wrong

**"Store the PDF content in the document."** Do not embed base64-encoded PDFs or large attachment content in Firestore documents. The 1 MB limit will bite you eventually, and every read of that document transfers the full payload. Store attachments in Cloud Storage, store the GCS URI in the Firestore document. The audit log references evidence; it does not contain evidence.

**"Use subcollections for everything relational."** Subcollections are not foreign keys. They are physically colocated data that shares a parent's access lifecycle. Line items within an order share the order's lifecycle — embedded array. Audit entries across all entities need independent querying — root collection. The test is not "is there a relationship?" (everything has relationships) but "is this data always accessed through its parent?"

**"One collection per entity type, always."** Sometimes the right answer is fewer collections. If `business_rules` and `memories` were both small (under 100 documents) and always queried together, merging them into a single `agent_context` collection with a `type` discriminator would reduce the number of reads. But they have fundamentally different access patterns — business rules are deterministic lookups, memories are embedding-based retrieval — so separate collections let you optimize each independently.

---

## Connections

This schema is the data foundation for every agent note in the research set. The [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] reads from `customers`, `products`, and `business_rules`; writes to `orders` and `audit_log`. The [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] reads from `suppliers`, `purchase_orders`, and `memories`; writes to `confirmations`, `purchase_orders`, and `audit_log`.

The event architecture that drives data flow into and out of these collections is detailed in [[Glacis-Agent-Reverse-Engineering-Event-Architecture]]. The item matching system that populates `products.embedding` and `products.aliases` is in [[Glacis-Agent-Reverse-Engineering-Item-Matching]]. The ERP integration patterns that determine whether Firestore is the source of truth or a cache are in [[Glacis-Agent-Reverse-Engineering-ERP-Integration]]. Security rules enforcement and the broader auth architecture are in [[Glacis-Agent-Reverse-Engineering-Security-Audit]].

## References

### Primary Sources
- **Glacis Order Intake Whitepaper** (Dec 2025) — Collection structure implied by "maps customer descriptions to internal item codes" and "validates against product/price master"
- **Glacis PO Confirmation Whitepaper** (March 2026) — "Every update fully auditable: the original email, what action the AI took, and who approved it"

### Web Research
- [Cloud Firestore Data Model — Firebase](https://firebase.google.com/docs/firestore/data-model) — Documents, collections, subcollections, references
- [Choose a Data Structure — Firebase](https://firebase.google.com/docs/firestore/manage-data/structure-data) — Subcollections vs nested data vs root-level collections
- [Firestore Best Practices: 15 Rules for Scalable Database Design — FireSchema](https://fireschema.vercel.app/en/learn/firestore-best-practices) — Document size guidelines, denormalization patterns
- [Advanced Firestore NoSQL Data Structure Examples — Fireship](https://fireship.io/lessons/advanced-firestore-nosql-data-structure-examples/) — Maps vs arrays, composite indexes, collection group queries
- [Data Modeling Basics for Cloud Firestore — Medium](https://medium.com/@louisjaphethkouassi/data-modeling-basics-for-cloud-firestore-2a5f68c3a536) — Denormalization strategies
- [Firestore Query Performance Best Practices — Estuary](https://estuary.dev/blog/firestore-query-best-practices/) — Index optimization, query planning
