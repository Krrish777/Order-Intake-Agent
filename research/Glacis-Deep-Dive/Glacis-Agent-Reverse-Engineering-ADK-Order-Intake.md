---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "ADK Agent: Order Intake Implementation"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - adk
  - order-intake
  - implementation
  - python
---

# ADK Agent: Order Intake Implementation

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 4 (Build-Level Detail)
> Parents: [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]], [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]]
> Foundation: [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]]

## The Problem

The Level 1 note ([[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]]) reverse-engineered *what* the Order Intake Agent does: classify incoming email, extract structured order data from arbitrary formats, validate against master data, route to auto-execute/clarify/escalate. This note answers a different question: *how do you define that agent in code using Google's Agent Development Kit?*

ADK gives you three primitives that matter here: `LlmAgent` (a single agent backed by a Gemini model with tools and instructions), `SequentialAgent` (a pipeline that runs sub-agents in strict order, passing data through shared session state), and `FunctionTool` (a wrapper that turns any Python function into a tool the LLM can call). The Order Intake workflow is a four-stage sequential pipeline — classify, extract, validate, route — where each stage's output feeds the next through `output_key` state injection. This is the canonical use case for `SequentialAgent`.

The challenge is not "can ADK express this?" — it can. The challenge is designing the boundaries between agents, choosing which operations are LLM calls vs deterministic tools, and structuring the shared state so each agent gets exactly the context it needs without drowning in irrelevant data.

## First Principles

An ADK agent pipeline is a directed acyclic graph of LLM calls and tool invocations, connected by a shared session state dictionary. Three rules govern the design:

**1. Each agent owns one decision.** The classifier decides "is this an order?" The extractor decides "what are the line items?" The validator decides "does this match master data?" The router decides "auto-execute, clarify, or escalate?" When you mix decisions within a single agent, you lose the ability to debug, test, and audit each step independently. ADK's `SequentialAgent` enforces this separation structurally — each sub-agent runs in isolation, seeing only the session state, not the internals of other agents.

**2. Tools are deterministic; agents are probabilistic.** An LLM should not be doing database lookups, inventory checks, or price comparisons. Those are exact operations with exact answers. They belong in `FunctionTool` definitions — plain Python functions the LLM calls when it needs ground truth. The LLM's job is interpretation (parsing unstructured text), judgment (is this confidence score high enough?), and composition (writing the clarification email). Every time you let the LLM do arithmetic or database queries directly, you introduce hallucination risk for zero benefit.

**3. State is the contract between agents.** `output_key` writes a string to `session.state[key]`. The next agent reads it via `{key}` template substitution in its instruction. This means the data contract between agents is defined by what keys exist in state and what format they contain. Define these contracts explicitly. If the extractor writes JSON to `extracted_order`, the validator's instruction must know to parse JSON from `{extracted_order}`. Loose contracts produce brittle pipelines.

## How It Works

### The Pipeline Architecture

```python
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import FunctionTool

# The four sub-agents (defined below)
# Each writes to a specific output_key that the next agent reads

order_intake_pipeline = SequentialAgent(
    name="OrderIntakePipeline",
    description="Processes inbound customer order emails through classification, "
                "extraction, validation, and routing.",
    sub_agents=[
        classifier_agent,
        extractor_agent,
        validator_agent,
        router_agent,
    ],
)
```

That is the entire top-level definition. Four agents, strict order, shared state. The complexity lives inside each sub-agent and its tools.

### Stage 1: ClassifierAgent

The classifier receives the raw email (subject + body + attachment filenames) and categorizes it. This is a lightweight, fast call — Gemini Flash, no tools needed. The LLM reads the email and outputs one of four labels.

```python
classifier_agent = LlmAgent(
    name="ClassifierAgent",
    model="gemini-2.0-flash",
    instruction="""You are an email classifier for a manufacturer's order inbox.

Classify the following email into exactly one category:
- ORDER: Customer is placing a new order or reorder
- INQUIRY: Customer is asking about products, pricing, or availability
- FOLLOWUP: Customer is replying to an existing order or conversation
- IRRELEVANT: Spam, internal forwards, complaints, or non-order content

Email subject: {email_subject}
Email body: {email_body}
Attachment filenames: {attachment_names}

Respond with ONLY the category label and a one-sentence rationale.
Example: "ORDER — Customer lists specific quantities and requests delivery by May 15."
""",
    output_key="classification_result",
)
```

**Design decisions:**
- No tools. Classification is pure LLM judgment on text — adding tools would slow this down for no benefit.
- Gemini Flash, not Pro. This is a simple text classification task. Flash handles it in <1 second at 1/10th the cost.
- The rationale is part of the output because it feeds into the audit trail. When a human reviews why an email was or was not processed, they see the agent's reasoning.
- `output_key="classification_result"` — the validator and router both read this downstream.

The initial state (`email_subject`, `email_body`, `attachment_names`) is populated by the Cloud Run handler that receives the Pub/Sub notification from Gmail API. Before invoking the pipeline, the handler writes these keys to session state:

```python
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner

session_service = InMemorySessionService()
session = await session_service.create_session(
    app_name="order_intake",
    user_id="system",
)

# Populate initial state from Gmail API payload
session.state["email_subject"] = email_data["subject"]
session.state["email_body"] = email_data["body_text"]
session.state["attachment_names"] = ", ".join(email_data["attachment_filenames"])
session.state["email_id"] = email_data["message_id"]
session.state["sender_email"] = email_data["from"]
session.state["attachment_uris"] = email_data["gcs_attachment_uris"]  # GCS paths

runner = Runner(
    agent=order_intake_pipeline,
    app_name="order_intake",
    session_service=session_service,
)
```

### Stage 2: ExtractorAgent

The extractor is the most expensive call in the pipeline. It takes the full email plus all attachments (PDFs, Excel files, scanned images) and extracts structured order data. This is where Gemini Pro earns its cost — multimodal understanding across text, tables, and images in a single call.

```python
def extract_order_fields(
    email_body: str,
    attachment_uris: str,
) -> str:
    """Extract structured order data from email body and attachments.

    Sends the email text and all attachment files to Gemini Pro
    for multimodal extraction. Returns a JSON string with order
    header fields and line items.
    """
    import json
    from google import genai

    client = genai.Client()

    # Build content parts: text body + file references
    parts = [f"Email body:\n{email_body}\n\nExtract all order information."]
    for uri in attachment_uris.split(","):
        uri = uri.strip()
        if uri:
            parts.append(genai.types.Part.from_uri(uri, mime_type="application/pdf"))

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=parts,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "customer_account": {"type": "string"},
                    "po_number": {"type": "string"},
                    "ship_to_address": {"type": "string"},
                    "special_instructions": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "customer_description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit": {"type": "string"},
                                "requested_price": {"type": "number"},
                                "requested_delivery_date": {"type": "string"},
                            },
                        },
                    },
                },
            },
        ),
    )
    return response.text

extract_order_tool = FunctionTool(func=extract_order_fields)
```

```python
extractor_agent = LlmAgent(
    name="ExtractorAgent",
    model="gemini-2.5-pro",
    instruction="""You are an order data extractor for a manufacturer.

The email has been classified as: {classification_result}

If the classification is not ORDER, output: {{"skipped": true, "reason": "Not an order"}}

If it IS an order, use the extract_order_fields tool with the email body
and attachment URIs to extract structured order data.

Email body: {email_body}
Attachment URIs: {attachment_uris}

After extraction, review the result for completeness. If critical fields
are missing (no line items, no customer identifier), note what is missing.
Output the complete extracted order JSON.
""",
    tools=[extract_order_tool],
    output_key="extracted_order",
)
```

**Design decisions:**
- The extraction itself is wrapped in a `FunctionTool` rather than relying on the agent's native Gemini call. This gives explicit control over the model version, response schema, and multimodal file handling. The agent orchestrates; the tool executes the heavy extraction.
- `response_schema` enforces the output structure. Without this, the LLM produces "creative" JSON with inconsistent field names across calls. With schema enforcement, every extraction returns the same shape. This is non-negotiable for downstream validation.
- The agent checks `{classification_result}` and short-circuits if the email is not an order. This prevents wasting a Pro-tier Gemini call on spam.

### Stage 3: ValidatorAgent

Validation is where deterministic tools dominate. The LLM's role is to orchestrate tool calls and interpret results — it does not do the validation logic itself.

```python
def validate_against_master(extracted_order_json: str) -> str:
    """Validate extracted order fields against master data in Firestore.

    Checks each line item's customer_description against the product
    master for SKU matching. Returns JSON with match results and
    confidence scores per line item.
    """
    import json
    from google.cloud import firestore

    db = firestore.Client()
    order = json.loads(extracted_order_json)
    results = []

    for item in order.get("line_items", []):
        desc = item["customer_description"]
        # Check exact alias match first
        alias_docs = (
            db.collection("product_aliases")
            .where("alias", "==", desc.lower().strip())
            .limit(1)
            .get()
        )
        if alias_docs:
            sku_data = alias_docs[0].to_dict()
            results.append({
                "customer_description": desc,
                "matched_sku": sku_data["sku"],
                "match_type": "exact_alias",
                "confidence": 0.99,
                "catalog_price": sku_data.get("price"),
            })
        else:
            # Vector similarity search against product embeddings
            vector_results = (
                db.collection("products")
                .find_nearest(
                    vector_field="description_embedding",
                    query_vector=_embed(desc),
                    limit=3,
                    distance_measure=firestore.Query.COSINE,
                )
            )
            matches = [doc.to_dict() for doc in vector_results]
            if matches and matches[0].get("distance", 1.0) < 0.08:
                results.append({
                    "customer_description": desc,
                    "matched_sku": matches[0]["sku"],
                    "match_type": "embedding",
                    "confidence": round(1 - matches[0]["distance"], 3),
                    "catalog_price": matches[0].get("price"),
                    "alternatives": [m["sku"] for m in matches[1:]],
                })
            else:
                results.append({
                    "customer_description": desc,
                    "matched_sku": None,
                    "match_type": "no_match",
                    "confidence": 0.0,
                })

    return json.dumps({"item_matches": results})


def check_inventory(sku_list_json: str) -> str:
    """Check real-time inventory availability for a list of SKUs.

    Returns available quantity and warehouse location for each SKU.
    """
    import json
    from google.cloud import firestore

    db = firestore.Client()
    skus = json.loads(sku_list_json)
    availability = []

    for sku in skus:
        doc = db.collection("inventory").document(sku).get()
        if doc.exists:
            data = doc.to_dict()
            availability.append({
                "sku": sku,
                "available_qty": data["available_quantity"],
                "warehouse": data["warehouse_id"],
                "lead_time_days": data.get("lead_time_days", 5),
            })
        else:
            availability.append({
                "sku": sku,
                "available_qty": 0,
                "warehouse": None,
                "lead_time_days": None,
            })

    return json.dumps({"inventory": availability})


validate_master_tool = FunctionTool(func=validate_against_master)
check_inventory_tool = FunctionTool(func=check_inventory)
```

```python
validator_agent = LlmAgent(
    name="ValidatorAgent",
    model="gemini-2.0-flash",
    instruction="""You are a validation engine for incoming customer orders.

You have the extracted order data: {extracted_order}

Your job:
1. Use validate_against_master to check each line item against the product
   master. Pass the full extracted order JSON.
2. For all matched SKUs, use check_inventory to verify availability.
   Pass a JSON list of matched SKU strings.
3. For each line item, determine a validation status:
   - PASS: SKU matched with confidence >= 0.92, inventory available,
     price within 2% of catalog
   - WARN: SKU matched but confidence 0.80-0.92, or minor price
     discrepancy, or partial inventory
   - FAIL: No SKU match, or inventory zero, or price discrepancy > 5%

Output a JSON object with:
- "validation_summary": overall PASS/WARN/FAIL
- "line_validations": array with status and details per line item
- "issues": array of human-readable issue descriptions
""",
    tools=[validate_master_tool, check_inventory_tool],
    output_key="validation_result",
)
```

**Design decisions:**
- Back to Gemini Flash. The validator agent does not need multimodal capability or deep reasoning — it calls deterministic tools and aggregates results. Flash keeps latency under 3 seconds for this stage.
- The validation thresholds (0.92 confidence, 2% price tolerance) are hardcoded in the instruction here for clarity. In production, these come from the SOP Playbook in Firestore (see [[Glacis-Agent-Reverse-Engineering-SOP-Playbook]]) and get injected as state variables.
- Inventory check is a separate tool from master validation because they hit different Firestore collections and have different failure modes. Keeping them separate means the agent can validate item matches even if the inventory service is temporarily down.

### Stage 4: RouterAgent

The router makes the final disposition decision and executes it. This is the only agent that takes external action — creating orders, sending emails.

```python
def create_order(order_json: str) -> str:
    """Create a sales order in Firestore (ERP staging).

    Writes the validated order as a new document in the sales_orders
    collection. Returns the order ID and confirmation status.
    """
    import json
    import uuid
    from datetime import datetime, timezone
    from google.cloud import firestore

    db = firestore.Client()
    order = json.loads(order_json)

    order_id = f"SO-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "order_id": order_id,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_email_id": order.get("email_id"),
        "customer_account": order.get("customer_account"),
        "po_number": order.get("po_number"),
        "line_items": order.get("line_items", []),
        "auto_created": True,
    }
    db.collection("sales_orders").document(order_id).set(doc)
    return json.dumps({"order_id": order_id, "status": "created"})


def send_confirmation(customer_email: str, order_summary: str) -> str:
    """Send order confirmation email to the customer.

    Uses Gmail API to send a professional confirmation from the
    orders inbox, including all line items and expected delivery dates.
    """
    from googleapiclient.discovery import build
    import base64
    from email.mime.text import MIMEText

    service = build("gmail", "v1")
    message = MIMEText(order_summary)
    message["to"] = customer_email
    message["subject"] = "Order Confirmation"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return '{"status": "confirmation_sent"}'


def flag_for_review(
    email_id: str,
    issues: str,
    extracted_order: str,
) -> str:
    """Flag an order for human review in the escalation queue.

    Creates a review task in Firestore with the original email reference,
    extracted data, and specific issues requiring human judgment.
    """
    import json
    from datetime import datetime, timezone
    from google.cloud import firestore

    db = firestore.Client()
    task = {
        "email_id": email_id,
        "issues": json.loads(issues),
        "extracted_order": json.loads(extracted_order),
        "status": "pending_review",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "priority": "high" if "FAIL" in issues else "medium",
    }
    ref = db.collection("review_queue").add(task)
    return json.dumps({"review_task_id": ref[1].id, "status": "queued"})


create_order_tool = FunctionTool(func=create_order)
send_confirmation_tool = FunctionTool(func=send_confirmation)
flag_for_review_tool = FunctionTool(func=flag_for_review)
```

```python
router_agent = LlmAgent(
    name="RouterAgent",
    model="gemini-2.0-flash",
    instruction="""You are the routing engine for the Order Intake pipeline.

Classification: {classification_result}
Extracted order: {extracted_order}
Validation result: {validation_result}
Customer email: {sender_email}
Email ID: {email_id}

Based on the validation result, take ONE of these actions:

**AUTO-EXECUTE** (validation_summary is PASS):
1. Call create_order with the validated order data
2. Call send_confirmation with the customer email and a professional
   summary of their order (items, quantities, prices, expected delivery)

**CLARIFY** (validation_summary is WARN with resolvable issues):
1. Call send_confirmation with a clarification request — be specific
   about what needs confirmation (e.g., "Did you mean SKU-7042 or
   SKU-7043?", "Your requested price of $12.50 differs from your
   contract price of $12.75 — please confirm.")
2. Call flag_for_review with priority "medium" to track the clarification

**ESCALATE** (validation_summary is FAIL):
1. Call flag_for_review with all issues and extracted data
2. Do NOT send any email to the customer — a human will handle this

Output a JSON summary of the action taken, including the route chosen
and any IDs generated (order_id or review_task_id).
""",
    tools=[create_order_tool, send_confirmation_tool, flag_for_review_tool],
    output_key="routing_result",
)
```

### Callbacks: Audit Logging

Every tool invocation in the pipeline needs an audit trail. ADK's `before_tool_callback` and `after_tool_callback` are the mechanism. Define them once, attach to every agent that has tools.

```python
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger("order_intake_audit")


def audit_before_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict]:
    """Log every tool invocation before execution for audit trail."""
    logger.info(
        "TOOL_CALL_START | agent=%s | tool=%s | args_keys=%s | time=%s",
        tool_context.agent_name,
        tool.name,
        list(args.keys()),
        datetime.now(timezone.utc).isoformat(),
    )
    return None  # Proceed with execution


def audit_after_tool(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict,
) -> Optional[Dict]:
    """Log tool results and persist to Firestore audit collection."""
    logger.info(
        "TOOL_CALL_END | agent=%s | tool=%s | time=%s",
        tool_context.agent_name,
        tool.name,
        datetime.now(timezone.utc).isoformat(),
    )
    # Persist to Firestore for permanent audit
    from google.cloud import firestore
    db = firestore.Client()
    db.collection("audit_log").add({
        "agent": tool_context.agent_name,
        "tool": tool.name,
        "args": args,
        "result_summary": str(tool_response)[:500],
        "timestamp": datetime.now(timezone.utc),
        "session_id": tool_context.session.id if tool_context.session else None,
    })
    return None  # Don't modify the response
```

Attach these callbacks to the agents that use tools:

```python
extractor_agent.before_tool_callback = audit_before_tool
extractor_agent.after_tool_callback = audit_after_tool
validator_agent.before_tool_callback = audit_before_tool
validator_agent.after_tool_callback = audit_after_tool
router_agent.before_tool_callback = audit_before_tool
router_agent.after_tool_callback = audit_after_tool
```

### State Flow Summary

Here is the complete data flow through session state:

```
Initial state (set by Cloud Run handler):
  email_subject, email_body, attachment_names,
  email_id, sender_email, attachment_uris

ClassifierAgent reads: email_subject, email_body, attachment_names
ClassifierAgent writes: classification_result
  → "ORDER — Customer lists 3 line items with delivery dates."

ExtractorAgent reads: classification_result, email_body, attachment_uris
ExtractorAgent writes: extracted_order
  → '{"customer_name": "Acme Foods", "po_number": "PO-4821", ...}'

ValidatorAgent reads: extracted_order
ValidatorAgent writes: validation_result
  → '{"validation_summary": "PASS", "line_validations": [...]}'

RouterAgent reads: classification_result, extracted_order,
                   validation_result, sender_email, email_id
RouterAgent writes: routing_result
  → '{"route": "AUTO_EXECUTE", "order_id": "SO-3F8A2B1C"}'
```

Each agent sees only the state keys referenced in its instruction template. The `SequentialAgent` guarantees execution order. There is no possibility of the router running before validation completes — this is structural, not dependent on careful coding.

## The Tradeoffs

**Four agents vs one mega-agent.** A single LlmAgent with all six tools could theoretically do the entire pipeline in one call. It would be cheaper (one LLM invocation instead of four) and simpler to deploy. But it would be impossible to debug when something goes wrong. Which step failed? Was it the classification, the extraction, or the validation? With four agents, you inspect `classification_result`, `extracted_order`, `validation_result`, and `routing_result` independently. In production, this visibility is worth the extra LLM calls.

**Gemini Pro for extraction vs Flash for everything.** The extraction stage accounts for 60-70% of the pipeline's cost because it uses Pro with multimodal inputs. You could use Flash for extraction too — it handles simple PDFs well. But the long tail of messy inputs (scanned faxes, handwritten notes, multi-page Excel files with merged cells) is where Pro's reasoning capability matters. The Level 1 note documents Knorr-Bremse achieving >99% accuracy — that level requires Pro on the hard cases. A cost-optimized production deployment would route simple text-only emails through Flash and complex multi-attachment emails through Pro, using the classifier output to decide.

**FunctionTool vs native LLM capability.** The validator agent could theoretically "reason" about whether a price is within tolerance without a tool call — just include the catalog price in the prompt and let the LLM compare. But LLMs make arithmetic errors. A FunctionTool that does `abs(requested - catalog) / catalog < 0.02` is correct every time. Every deterministic check should be a tool. The LLM orchestrates tool calls and interprets results — it does not replace them.

**In-memory session vs persistent state.** The example uses `InMemorySessionService` for simplicity. In production, you need `DatabaseSessionService` backed by Firestore so that if the Cloud Run instance crashes mid-pipeline, you can resume from the last completed stage. ADK supports this — swap the session service, keep everything else identical.

## What Most People Get Wrong

**"Just use one agent with a long system prompt."** This works for demos. It fails in production because you cannot independently test, monitor, or version individual pipeline stages. When extraction accuracy drops, you need to update the extractor without touching the validator. When a new validation rule is added, you need to test it in isolation. The SequentialAgent structure gives you this modularity for free.

**"The LLM should decide the routing logic."** No. The LLM should *execute* the routing decision, but the decision criteria (confidence thresholds, price tolerances, escalation rules) should be deterministic and configurable. Putting business rules inside an LLM instruction means they drift with prompt changes, cannot be audited, and are invisible to business users. The SOP Playbook (see [[Glacis-Agent-Reverse-Engineering-SOP-Playbook]]) stores these rules as structured data. The agent reads them; it does not invent them.

**"output_key handles complex data well."** It does not. `output_key` stores the agent's *final text response* as a string. If the extractor outputs a complex JSON object, it is stored as a string. The validator must parse it. This works, but it means you are dependent on the LLM producing valid JSON every time. Use `response_schema` enforcement on the Gemini call inside the FunctionTool (as shown in the extractor) to guarantee valid JSON. The agent's `output_key` then stores the tool's validated output, not free-form LLM text.

**"Callbacks slow down the pipeline."** The audit callbacks add ~50ms per tool call (one Firestore write). On a pipeline that takes 10-30 seconds total, this is noise. The alternative — reconstructing what happened from Cloud Logging after something goes wrong — costs hours of debugging time. Every production agent needs audit callbacks from day one.

## Connections

- [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] — Level 1 foundation that this note implements in code
- [[Glacis-Agent-Reverse-Engineering-Email-Ingestion]] — The Gmail API + Pub/Sub architecture that feeds initial state into this pipeline
- [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]] — The validation rules and enrichment logic that the ValidatorAgent's tools implement
- [[Glacis-Agent-Reverse-Engineering-Generator-Judge]] — The Generator-Judge pattern used within the ExtractorAgent (extract + validate = generate + judge)
- [[Glacis-Agent-Reverse-Engineering-ADK-PO-Confirmation]] — Sibling implementation note for the PO Confirmation agent — same ADK patterns, different domain
- [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] — The collections (`sales_orders`, `review_queue`, `product_aliases`, `inventory`, `audit_log`) referenced by the FunctionTools
- [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] — Pub/Sub topic design that triggers this pipeline
- [[Glacis-Agent-Reverse-Engineering-Prompt-Templates]] — The extraction and classification prompts refined for production use
- [[Glacis-Agent-Reverse-Engineering-Overview]] — Full research map

## Subtopics for Further Deep Dive

| # | Subtopic | Why It Matters |
|---|----------|----------------|
| 1 | Gemini Prompt Templates | The extraction prompt shown here is a starting point. Production prompts need few-shot examples, format-specific instructions, and edge case handling for merged cells, multi-currency, and partial orders. See [[Glacis-Agent-Reverse-Engineering-Prompt-Templates]]. |
| 2 | Firestore Data Model | The `sales_orders`, `product_aliases`, `inventory`, `review_queue`, and `audit_log` collections need indexes, security rules, and TTL policies. See [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]]. |
| 3 | Error Recovery | What happens when a tool call fails mid-pipeline? ADK does not auto-retry. You need `before_tool_callback` to implement circuit breaker patterns. See [[error-recovery-patterns]]. |
| 4 | Token Optimization | The extraction stage sends full PDFs to Gemini Pro. A 15-page attachment costs ~30K tokens. Strategies: pre-filter pages, extract tables before sending, use Flash for triage. See [[Glacis-Agent-Reverse-Engineering-Token-Optimization]]. |

## References

- [ADK SequentialAgent Documentation](https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/) — Constructor parameters, execution model, state sharing
- [ADK Multi-Agent Systems](https://google.github.io/adk-docs/agents/multi-agents/) — output_key patterns, agent communication via shared state
- [ADK Callback Types](https://google.github.io/adk-docs/callbacks/types-of-callbacks/) — before_tool_callback and after_tool_callback signatures and usage
- [Developer's Guide to Multi-Agent Patterns in ADK](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/) — Sequential, parallel, hierarchical, and generator-critic patterns
- [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] — Level 1 foundation with Glacis workflow, enterprise metrics, and design rationale
