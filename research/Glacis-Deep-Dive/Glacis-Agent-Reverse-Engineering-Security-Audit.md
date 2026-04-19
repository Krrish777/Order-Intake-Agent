---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Security and Audit Architecture"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 3
date: 2026-04-08
tags:
  - research
  - supply-chain
  - security
  - audit-trail
  - enterprise
  - compliance
---

# Security and Audit Architecture

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]]. Depth level: 3. Parent: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]]

Glacis's sales pitch includes three claims that enterprise procurement teams care about more than any feature demo: "No data leaves the environment. Every action logged with full audit trail. Procurement team controls what AI can and cannot do." These are not marketing fluff — they map directly to SOC 2 Type II and ISO 27001 requirements that any enterprise customer's security team will ask about before signing a contract. In 2026, SOC 2 audits increasingly evaluate zero-trust security, advanced identity management, and automation of compliance workflows. AI agents add a new dimension: auditors now scrutinize autonomous data processing patterns, not just human access logs.

For the hackathon build, we do not need SOC 2 certification. We need a security architecture that demonstrates we understand what enterprise-grade means — and that we built the foundations correctly so that certification is a matter of process, not redesign. This note designs that architecture.

---

## The Problem

### AI Agents Create New Security Surfaces

A traditional web application has a well-understood security model: users authenticate, RBAC controls what they can access, every HTTP request is logged, and data flows through defined endpoints. An AI agent breaks every one of these assumptions.

**The agent acts autonomously.** It reads emails, extracts data, makes routing decisions, and writes to the ERP without a human clicking "submit." Who is responsible for an incorrect auto-executed order? The agent does not have a user session. It does not click through a UI with CSRF tokens. It operates on a service account with broad permissions because it needs to read email, write to Firestore, call the Gemini API, and send outbound email — all in a single pipeline execution.

**The agent processes sensitive business data.** Purchase orders contain pricing (competitively sensitive), quantities (production planning intelligence), supplier names (relationship intelligence), and delivery schedules (operational intelligence). A data breach of PO data is not an abstract privacy concern — it is competitive intelligence handed to rivals.

**The agent makes decisions opaquely.** When a human CSR enters an order incorrectly, the error is traceable: the CSR misread the PDF, typed the wrong quantity, and the ERP audit log shows who entered what and when. When an AI agent makes the same error, the question is harder: which model version was used? What was in the prompt? What confidence score did the extraction return? Why did the routing logic auto-execute instead of escalate? Without a purpose-built audit trail, these questions are unanswerable.

**The agent compounds errors across chains.** Blaxel's SOC 2 compliance research identifies a critical pattern: "When one AI agent's incorrect output becomes input for another, errors compound in ways traditional processing integrity controls can't detect." In our architecture, the Order Intake agent's extraction output feeds the validation pipeline, which feeds the routing decision, which feeds the ERP write. A hallucinated quantity in extraction becomes a validated (but wrong) quantity if the hallucination falls within business rule bounds, becomes an auto-executed wrong order, becomes a wrong shipment. The audit trail must track the full chain, not just the final write.

### What Enterprise Buyers Actually Ask

From MintMCP's enterprise AI security guide, these are the questions that procurement security teams ask before approving an AI agent deployment:

1. **Where does our data go?** Does it leave our environment? Is it stored by the LLM provider? For how long?
2. **What can the agent do?** Can it delete records? Can it send emails to external parties? What are the permission boundaries?
3. **Can we see what it did?** Is there a complete audit trail? Can we reconstruct any decision the agent made?
4. **Can we stop it?** Is there a kill switch? Can we disable auto-execution and route everything to human review?
5. **Who controls the rules?** Can the procurement team change thresholds and business rules without an engineering deployment?

Glacis answers all five: data stays in the customer's environment (on-prem or private cloud), the SOP playbook controls what the agent can and cannot do, every action is logged, the confidence threshold can be set to 100% (routing everything to human review), and the playbook is editable by procurement teams through a dashboard. Our build must answer the same five questions.

---

## First Principles

Security for an AI agent system decomposes into four layers, each addressing a different threat:

**Layer 1: Data Isolation.** Where does data live and who can access it? This is the most basic question and the one enterprise buyers ask first. The answer must be: data stays within the project boundary. No PO data is stored by Google's Gemini API (Google's data governance policy for the paid API tier does not use customer data for model training). Firestore data is encrypted at rest and in transit. Service accounts follow least-privilege — the extraction service can read email and write to orders collection, but cannot read the metrics collection or modify the SOP playbook.

**Layer 2: Access Control.** Who can do what? This is RBAC applied to three actor types: the AI agent (service accounts with scoped permissions), human operators (authenticated users with role-based dashboard access), and administrators (who can modify the SOP playbook, adjust thresholds, and manage service accounts). The MintMCP enterprise guide stresses a critical distinction: "Many agents need only read access — write permissions require explicit justification." The extraction agent needs write access to the orders collection but should have read-only access to the product master. The validation agent needs read access to the price list but no write access anywhere except the validation results subcollection.

**Layer 3: Audit Trail.** What happened, when, and why? Every agent action is logged with five dimensions: **who** (which service account or user), **what** (the specific operation — extraction, validation, routing, ERP write), **when** (precise timestamp), **where** (which Firestore collection, which Gemini model endpoint), and **why** (the business context — order ID, customer, confidence score, routing reason). This is not application logging. This is forensic-grade evidence that auditors can use to reconstruct any decision chain.

**Layer 4: Control.** Can we stop it, change it, or override it? The SOP playbook from [[Glacis-Agent-Reverse-Engineering-SOP-Playbook]] is the control mechanism. Set the auto-execution confidence threshold to 1.0 and nothing auto-executes — every order goes to human review. Disable outbound email and the agent stops sending clarification requests. Block a specific customer's orders from auto-processing and they always route to a human. The control layer is what makes the agent acceptable to risk-averse enterprises: you can dial autonomy from zero to full and anywhere in between.

---

## How It Actually Works

### Audit Trail: Every Agent Action Logged

The audit trail is a Firestore collection (`audit_log`) where every meaningful agent action creates a document. Not every function call — that is application logging and belongs in Cloud Logging. The audit trail captures business-meaningful actions: an order was received, an extraction was performed, a validation passed or failed, a routing decision was made, an ERP record was created, a human override was recorded.

```python
from google.cloud import firestore
from datetime import datetime, timezone
import uuid

db = firestore.AsyncClient()

async def log_audit_event(
    action: str,
    actor: str,
    order_id: str,
    details: dict,
    outcome: str,
    confidence: float | None = None,
):
    """Write an immutable audit trail entry.

    Actions: order_received, extraction_completed, validation_completed,
             routing_decided, erp_write, human_override, email_sent,
             supplier_followup, confirmation_received, exception_created
    Actors: "agent:order-intake", "agent:po-confirmation",
            "user:jane.doe@company.com", "system:scheduler"
    Outcomes: "success", "failure", "escalated", "overridden"
    """
    event_id = str(uuid.uuid4())
    await db.collection("audit_log").document(event_id).set({
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor": actor,
        "order_id": order_id,
        "details": details,
        "outcome": outcome,
        "confidence": confidence,
        "environment": "production",  # or "staging", "demo"
        "agent_version": "1.2.0",
        "created_at": firestore.SERVER_TIMESTAMP,
    })
```

A single order generates 5-8 audit events. Here is the trail for a typical auto-executed order:

| # | Action | Actor | Details | Outcome |
|---|--------|-------|---------|---------|
| 1 | `order_received` | agent:order-intake | email_id, from, subject, attachment_count, format_detected | success |
| 2 | `extraction_completed` | agent:order-intake | model_used, tokens, line_item_count, per_field_confidence | success |
| 3 | `validation_completed` | agent:order-intake | rules_checked, rules_passed, rules_warned, rules_failed | success |
| 4 | `routing_decided` | agent:order-intake | decision: "auto_execute", reason: "all validations passed, confidence 0.96" | success |
| 5 | `erp_write` | agent:order-intake | firestore_doc_id, collection, fields_written | success |
| 6 | `email_sent` | agent:order-intake | to, subject, template_used: "order_confirmation" | success |

For an escalated order, the trail includes additional events:

| # | Action | Actor | Details | Outcome |
|---|--------|-------|---------|---------|
| 4 | `routing_decided` | agent:order-intake | decision: "escalate", reason: "price_mismatch: $12.50 vs contract $12.75" | escalated |
| 5 | `exception_created` | agent:order-intake | exception_type: "price_mismatch", assigned_to: "sales_team" | success |
| 6 | `human_override` | user:jane.doe@company.com | action: "approve_with_edit", field_changed: "unit_price", old: 12.50, new: 12.75 | overridden |
| 7 | `erp_write` | agent:order-intake | firestore_doc_id, note: "price corrected by operator" | success |

The `human_override` event is critical. It captures exactly what the human changed and why. This is the feedback loop for [[Glacis-Agent-Reverse-Engineering-Learning-Loop]] and the evidence that auditors need to verify that humans retain control over automated decisions.

### Firestore Security Rules: Defense in Depth

Firestore Security Rules enforce access control at the database level, independent of application code. Even if a service account token is compromised or application code has a bug, the rules prevent unauthorized operations.

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Audit log: append-only. Nobody deletes or updates audit entries.
    match /audit_log/{eventId} {
      allow create: if request.auth != null;
      allow read: if request.auth != null
                  && request.auth.token.role in ['admin', 'auditor', 'operator'];
      allow update, delete: if false;  // Immutable. Period.
    }

    // Orders: agents can create and update, operators can read and update
    match /orders/{orderId} {
      allow create: if request.auth != null
                    && request.auth.token.role in ['agent', 'admin'];
      allow read: if request.auth != null
                  && request.auth.token.role in ['agent', 'admin', 'operator'];
      allow update: if request.auth != null
                    && request.auth.token.role in ['agent', 'admin', 'operator'];
      allow delete: if request.auth != null
                    && request.auth.token.role == 'admin';
    }

    // SOP Playbook: only admins can modify business rules
    match /sop_playbook/{ruleId} {
      allow read: if request.auth != null;
      allow write: if request.auth != null
                   && request.auth.token.role == 'admin';
    }

    // Product master: agents read, admins write
    match /products/{productId} {
      allow read: if request.auth != null;
      allow write: if request.auth != null
                   && request.auth.token.role == 'admin';
    }

    // Metrics: agents write (counters), everyone reads
    match /metrics/{dateId} {
      allow read: if request.auth != null;
      allow write: if request.auth != null
                   && request.auth.token.role in ['agent', 'admin'];
    }
  }
}
```

The key constraint: `audit_log` is **append-only**. No updates, no deletes, regardless of role. This is non-negotiable for SOC 2 — if audit entries can be modified, the entire trail is untrustworthy. The Firestore rule `allow update, delete: if false` enforces this at the platform level, not the application level.

### RBAC for the Dashboard

The dashboard serves three roles with different permissions:

**Operator** (procurement team): Can view orders, view the exception queue, approve/edit/reject exceptions, view metrics. Cannot modify the SOP playbook, cannot access raw audit logs, cannot change agent configuration.

**Admin** (procurement manager or IT): Everything an operator can do, plus: modify SOP playbook rules, view full audit trail, manage user roles, configure confidence thresholds, enable/disable auto-execution.

**Auditor** (internal audit or external SOC 2 auditor): Read-only access to the full audit trail and metrics. Cannot modify anything. Cannot view raw order data beyond what the audit trail contains (data minimization for auditor access).

Implementation: Firebase Authentication with custom claims. When a user is provisioned, their role is set as a custom claim on their Firebase Auth token:

```python
from firebase_admin import auth

def set_user_role(uid: str, role: str):
    """Set RBAC role as Firebase custom claim."""
    assert role in ("operator", "admin", "auditor")
    auth.set_custom_user_claims(uid, {"role": role})
```

The Firestore Security Rules (above) read `request.auth.token.role` from these custom claims. The dashboard UI reads the same claims to show/hide features:

```javascript
import { getAuth } from "firebase/auth";

const user = getAuth().currentUser;
const tokenResult = await user.getIdTokenResult();
const role = tokenResult.claims.role;

// Show admin panel only for admins
if (role === "admin") {
    document.getElementById("admin-panel").style.display = "block";
}
// Show audit viewer only for admins and auditors
if (role === "admin" || role === "auditor") {
    document.getElementById("audit-viewer").style.display = "block";
}
```

### Data Isolation: What "No Data Leaves the Environment" Means

Glacis's claim maps to three concrete guarantees:

**Guarantee 1: LLM provider does not retain data.** Google's Gemini API (paid tier) does not use customer prompts or responses for model training. Data is processed and discarded. The Google Cloud Data Processing Terms govern this. For the hackathon build, this is handled by using the paid API tier, not the free tier (which has different data terms).

**Guarantee 2: Data at rest is encrypted.** Firestore encrypts all data at rest with Google-managed encryption keys by default. For higher assurance, use Customer-Managed Encryption Keys (CMEK) — the customer controls the key in Cloud KMS, and Google cannot decrypt the data without it. For a hackathon demo, default encryption is sufficient. For enterprise deployment, CMEK is the answer to "can Google read our PO data?"

**Guarantee 3: Data in transit is encrypted.** All Firestore, Gemini API, and Cloud Run traffic uses TLS 1.3. Internal Google Cloud traffic between services uses ALTS (Application Layer Transport Security). No unencrypted data transmission anywhere in the pipeline.

**Guarantee 4: Network isolation.** Cloud Run services can be deployed with ingress restrictions — only traffic from within the VPC or from specific Cloud Run services can reach the agent endpoints. The demo dashboard on Firebase Hosting communicates with Firestore directly (Firestore SDK, not through Cloud Run), so the agent's Cloud Run services can be fully internal.

### The Kill Switch

The most underrated security feature is the ability to stop everything immediately. Implementation is simple: a single document in Firestore (`config/agent_status`) with a `paused` boolean. Every agent pipeline checks this at the start:

```python
async def check_agent_status():
    """Kill switch: halt all processing if paused."""
    config = await db.collection("config").document("agent_status").get()
    if config.exists and config.to_dict().get("paused", False):
        raise AgentPausedError("Agent processing is paused by administrator")
```

The admin dashboard has a prominent "Pause Agent" button that sets `paused: true`. All in-flight orders complete their current step (you do not kill mid-extraction), but no new orders enter the pipeline. The audit trail logs the pause event: who paused it, when, and why.

This addresses the enterprise buyer's deepest fear: "What if it goes wrong and we can't stop it?" The answer is: one click, sub-second pause, full audit trail of the pause itself.

---

## The Tradeoffs

### Audit Trail Volume vs. Storage Cost

A full audit trail for 1,000 orders/day generates ~6,000-8,000 Firestore documents per day. At ~1KB per document, that is 6-8MB/day or ~2.5GB/year. Firestore storage costs $0.18/GB/month, so the annual cost is roughly $5.40. Trivial. The real cost concern is read operations when auditors query the trail — a query scanning 2 million audit documents costs ~$1.20 in Firestore reads. For frequent audit queries, consider archiving old audit data to BigQuery (batch export via Firestore Export) where analytical queries are cheaper.

### Security Rule Granularity vs. Development Velocity

Fine-grained Firestore Security Rules catch unauthorized access at the database level, but they also catch legitimate access patterns you forgot to allow. During development, overly strict rules slow you down — every new feature requires a rule update. The pragmatic approach: start with permissive rules in development (authenticated users can read/write everything), implement strict rules before the demo, and test them thoroughly. The audit_log append-only rule should be strict from day one — that is a constraint you want enforced early to catch any code that accidentally tries to update an audit entry.

### Enterprise-Grade Design vs. Hackathon Scope

Full SOC 2 compliance requires 5.5-17.5 months of preparation and ongoing audit. A hackathon demo requires a design that demonstrates understanding. The distinction: implement the audit trail (it is a Firestore collection with a write function — a few hours of work), implement RBAC (Firebase custom claims — another few hours), implement the kill switch (a single Firestore document check — 30 minutes), and design-but-do-not-implement CMEK, VPC Service Controls, and formal penetration testing. Show the architecture diagram with all layers. Implement the ones that take hours. Reference the ones that take months.

### Append-Only Audit vs. GDPR Right to Deletion

SOC 2 demands immutable audit trails. GDPR demands the right to erasure. These conflict directly when audit entries contain personal data (customer email addresses, supplier contact names). The resolution: audit entries reference order IDs and actor IDs, not raw personal data. The order document (which contains the personal data) can be anonymized or deleted. The audit entry survives with a reference to a now-deleted order — the trail is intact, the personal data is gone. This is a production concern, not a hackathon concern, but mentioning it in the architecture document demonstrates sophistication.

---

## What Most People Get Wrong

### Logging Everything vs. Logging the Right Things

Application-level logging (every function call, every variable value, every API response) is not an audit trail. It is noise that makes the actual audit trail harder to find. The audit trail captures business-meaningful actions: order received, extraction completed, decision made, record created, human override applied. Application logs capture technical details: HTTP status codes, retry attempts, cache hits, memory usage. Both are necessary. Mixing them into the same storage makes both useless. The audit trail goes to Firestore (structured, queryable, real-time). Application logs go to Cloud Logging (high-volume, searchable, ephemeral).

### Trusting Application Code for Access Control

"The dashboard code checks the user's role before showing the admin panel, so we have access control." No. Client-side role checks are a UX feature, not a security control. A user can modify JavaScript in the browser, call the Firestore API directly, or use a REST client to bypass the dashboard entirely. Firestore Security Rules enforce access control at the database level — they execute on Google's servers, not in the user's browser. The dashboard's role checks improve usability (operators do not see admin features they cannot use). The security rules enforce security (operators cannot perform admin operations even if they try).

### Assuming the LLM Is Trusted

The Gemini API is an external service. It receives your PO data, processes it, and returns structured output. Treating it as a trusted internal service is a security modeling error. The correct model: Gemini is an untrusted processor that receives the minimum data needed for its task and returns output that must be validated before acting on it. Do not send the full customer master to Gemini when all it needs is the product catalog subset relevant to this order. Do not trust Gemini's extraction output without validation — not because Gemini is malicious, but because LLM output is inherently probabilistic and the validation pipeline from [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]] exists precisely to catch errors regardless of source.

### Skipping the Human Override Trail

Logging agent actions is necessary but insufficient. The most valuable audit entries are human overrides — the moments where a human operator disagreed with the agent and changed something. These entries serve three purposes: they provide the feedback data for the learning loop, they demonstrate to auditors that humans retain control, and they identify patterns where the agent consistently gets something wrong (suggesting a prompt or rule change is needed). If human overrides are not logged with the same rigor as agent actions, you lose all three benefits.

### Designing Security for the Happy Path Only

"The agent reads email, extracts data, writes to Firestore. We secured those three operations." What about error paths? When extraction fails, does the error message contain raw PO data that gets logged to Cloud Logging (which has different access controls than Firestore)? When a retry occurs, does the retry mechanism re-read the email and potentially double-process, creating duplicate orders? When the agent is paused and resumed, does it replay the backlog or skip it? Security architecture must cover failure modes, not just success modes. Every error path is a potential data leak or integrity violation.

---

## Connections

- **Parent**: [[Glacis-Agent-Reverse-Engineering-ERP-Integration]] — ERP sync operations are the highest-risk actions the agent performs; security rules and audit trail are critical here
- **Sibling**: [[Glacis-Agent-Reverse-Engineering-Metrics-Dashboard]] — metrics collection is the operational counterpart to audit logging; both use Firestore as the data layer
- **Sibling**: [[Glacis-Agent-Reverse-Engineering-SOP-Playbook]] — the SOP playbook is the control mechanism (Layer 4); admin RBAC governs who can modify it
- **Child**: [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] — Firestore collection design must incorporate the audit_log collection and security rules
- **Related**: [[Glacis-Agent-Reverse-Engineering-Learning-Loop]] — human override audit entries are the primary input to the learning loop
- **Related**: [[Glacis-Agent-Reverse-Engineering-Overview]] — Glacis's SOC 2 + ISO 27001 claims and "no data leaves the environment" promise
- **Wiki**: [[firestore]] — Firestore Security Rules, custom claims, encryption at rest
- **Wiki**: [[firebase]] — Firebase Authentication, custom claims for RBAC
- **Wiki**: [[cloud-run]] — ingress restrictions, service accounts, VPC connectivity
- **Wiki**: [[error-recovery-patterns]] — error path security considerations

---

## Subtopics for Further Deep Dive

1. **Firestore Security Rules Testing** — Unit testing security rules with the Firebase Emulator Suite; coverage of all RBAC scenarios; testing that append-only constraint holds under edge cases; CI/CD integration for rule deployment
2. **Data Retention and Archival Policy** — When to archive audit logs to BigQuery; retention periods for different data types; GDPR compliance for personal data in audit trails; automated archival pipelines
3. **Incident Response Playbook** — What happens when the agent processes an order incorrectly at scale; detection (metrics anomaly triggers alert), containment (kill switch), investigation (audit trail reconstruction), remediation (batch correction), and post-mortem (root cause and prevention)
4. **Service Account Least-Privilege Design** — Separate service accounts for each pipeline stage (extraction, validation, routing, ERP write); IAM bindings that enforce minimum permissions; rotation policy; key management
5. **Prompt Injection Mitigation** — What happens when a malicious customer sends an email designed to manipulate the extraction prompt; input sanitization before LLM calls; output validation as defense-in-depth; Gemini's built-in safety filters as first layer

---

## References

- Glacis, "How AI Automates Order Intake in Supply Chain," Dec 2025 — "No data leaves the environment. Every action logged with full audit trail."
- Glacis, "AI For PO Confirmation V8," March 2026 — SOC 2 Type II and ISO 27001 compliance claims, configurable SOP playbook for procurement team control
- [MintMCP, "AI Agent Security: The Complete Enterprise Guide for 2026"](https://www.mintmcp.com/blog/ai-agent-security) — Five-dimension audit trail (who, what, when, where, why), 33% of organizations lack audit trails for AI activity, RBAC for AI agents, kill switch requirement, organizations with proper audit trails 20-32 points ahead on maturity metrics
- [Blaxel, "SOC 2 Compliance for AI Agents in 2026"](https://blaxel.ai/blog/soc-2-compliance-ai-guide) — AI agent error compounding across chains, traditional audit trails inadequate for AI systems, runtime code execution risks, 5.5-17.5 month certification timeline, append-only logging as non-negotiable
- [Konfirmity, "What Changed in SOC 2 for 2026?"](https://www.konfirmity.com/blog/soc-2-what-changed-in-2026) — Zero-trust security evaluation, dynamic authorization, continuous monitoring expectations for 2026 audits
- Google Cloud, "Firestore Security Rules" — Rule syntax, custom claims integration, append-only patterns
- Google Cloud, "Data Processing Terms" — Paid API tier data handling: no customer data used for model training
- Firebase Documentation, "Custom Claims and Security Rules" — RBAC implementation with Firebase Auth custom claims
