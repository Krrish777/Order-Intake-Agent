---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "ADK Agent: PO Confirmation Implementation"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - adk
  - po-confirmation
  - implementation
  - python
---

# ADK Agent: PO Confirmation Implementation

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]] deep dive. Depth level: 4 (Build-Level Detail)
> Parent: [[Glacis-Agent-Reverse-Engineering-Supplier-Communication]]
> Foundation: [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]]

## The Problem

The Order Intake pipeline ([[Glacis-Agent-Reverse-Engineering-ADK-Order-Intake]]) is a single-pass operation: email arrives, pipeline runs, order is created or escalated. The PO Confirmation agent is fundamentally different. It is a **stateful, long-running process** that tracks a purchase order across days or weeks through multiple state transitions: PO sent to supplier, follow-up reminders triggered by SLA timers, supplier response received and parsed, discrepancies detected and clarified, ERP updated or buyer escalated. A single PO might cycle through the agent three or four times before resolution.

This changes the ADK architecture. The Order Intake pipeline is one `SequentialAgent` invocation per email. The PO Confirmation agent is a `SequentialAgent` invoked multiple times against the same persistent session state, with the entry point determined by a state machine. The agent does not start from scratch each time — it reads the current PO state, determines which stage to execute, and advances the state machine accordingly.

ADK handles this through its session service. The session state persists between invocations. The PO document in Firestore tracks the current state (`po_sent`, `awaiting_response`, `response_received`, `validated`, `confirmed`, `escalated`). Each invocation reads the state, runs the appropriate sub-pipeline, and writes the new state. Cloud Scheduler triggers periodic checks for overdue POs. Gmail Pub/Sub triggers processing when a supplier replies.

## First Principles

The PO Confirmation agent is a **state machine with LLM-powered transitions**. The states are deterministic. The transitions between states require LLM capability (parsing unstructured supplier emails, composing follow-up messages) but the decision of which transition to take is governed by rules, not LLM judgment.

Three architectural principles:

**1. State drives behavior, not the LLM.** The agent reads the PO's current state from Firestore before doing anything. If the state is `awaiting_response` and the SLA has expired, the agent sends a follow-up — no LLM reasoning needed to make that decision. If the state is `response_received`, the agent runs the parse-match-detect pipeline. The LLM is a tool within the state machine, not the state machine itself. This is critical because state machines are testable, auditable, and deterministic. LLM-driven control flow is none of those things.

**2. Every invocation is idempotent.** The same supplier email processed twice should produce the same result and not create duplicate ERP updates. This means the agent checks "has this email already been processed?" before doing work. Firestore's document IDs based on email message IDs enforce this. The `before_tool_callback` on the `update_erp` tool checks for existing confirmations before writing.

**3. The agent never negotiates.** This boundary is a feature. The agent parses, validates, and communicates factual discrepancies. It does not say "we'll accept $12.80 instead of $12.50." Commercial decisions route to the buyer. The system instruction for every sub-agent reinforces this constraint explicitly.

## How It Works

### The State Machine

```
po_sent ──────────────────┐
    │                     │
    ▼                     │ (SLA expired, no response)
awaiting_response ◄───────┘
    │         │
    │         ▼ (SLA expired again)
    │    followup_sent ───► awaiting_response
    │
    ▼ (supplier email received)
response_received
    │
    ▼
validating
    │
    ├──► confirmed ──► erp_updated
    │
    ├──► discrepancy_detected ──► clarification_sent ──► awaiting_response
    │
    └──► escalated ──► pending_buyer_action ──► erp_updated
```

Each state transition is an event. Each event triggers an ADK pipeline invocation. The pipeline reads current state, processes, writes new state.

### The Pipeline Architecture

Unlike Order Intake's single pipeline, PO Confirmation uses a **dispatcher pattern** — a thin entry agent reads state and delegates to the appropriate sub-pipeline.

```python
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import FunctionTool


# Sub-pipeline for parsing + matching + detecting discrepancies
response_processing_pipeline = SequentialAgent(
    name="ResponseProcessingPipeline",
    description="Processes a supplier response: parse, match against PO, "
                "detect discrepancies, and determine action.",
    sub_agents=[
        response_parser_agent,
        po_matcher_agent,
        discrepancy_detector_agent,
        action_agent,
    ],
)
```

The dispatcher is a Cloud Run handler, not an ADK agent, because the routing decision is purely deterministic:

```python
async def handle_po_event(event_type: str, po_id: str, email_data: dict = None):
    """Entry point for all PO Confirmation events.

    Called by:
    - Gmail Pub/Sub: when a supplier reply arrives (event_type="email_received")
    - Cloud Scheduler: periodic SLA check (event_type="sla_check")
    - Dashboard: buyer action completed (event_type="buyer_action")
    """
    from google.cloud import firestore
    from google.adk.runners import Runner
    from google.adk.sessions import DatabaseSessionService

    db = firestore.Client()
    po_doc = db.collection("purchase_orders").document(po_id).get()
    po_state = po_doc.to_dict()["status"]

    session_service = DatabaseSessionService(db)
    session = await session_service.get_or_create_session(
        app_name="po_confirmation",
        user_id="system",
        session_id=f"po-{po_id}",
    )

    if event_type == "sla_check" and po_state == "awaiting_response":
        # Check if SLA expired
        sla_hours = po_state_data.get("sla_hours", 48)
        if _hours_since(po_doc.to_dict()["last_sent_at"]) > sla_hours:
            session.state["po_data"] = po_doc.to_dict()
            session.state["action_needed"] = "send_followup"
            runner = Runner(
                agent=followup_agent,
                app_name="po_confirmation",
                session_service=session_service,
            )
            await _run_agent(runner, session)

    elif event_type == "email_received" and email_data:
        # Supplier replied — run the full processing pipeline
        session.state["po_data"] = po_doc.to_dict()
        session.state["supplier_email_body"] = email_data["body_text"]
        session.state["supplier_attachment_uris"] = email_data.get("gcs_uris", "")
        session.state["supplier_email_id"] = email_data["message_id"]
        session.state["po_id"] = po_id

        runner = Runner(
            agent=response_processing_pipeline,
            app_name="po_confirmation",
            session_service=session_service,
        )
        await _run_agent(runner, session)

    elif event_type == "buyer_action":
        session.state["po_data"] = po_doc.to_dict()
        session.state["buyer_decision"] = email_data.get("decision")
        runner = Runner(
            agent=action_agent,
            app_name="po_confirmation",
            session_service=session_service,
        )
        await _run_agent(runner, session)
```

### Stage 1: ResponseParserAgent

The supplier reply can be plain text ("Confirmed, will ship by May 20"), a PDF order acknowledgment, an Excel spreadsheet with line-by-line confirmations, or a screenshot of their ERP system. The parser handles all of these.

```python
def parse_supplier_response(
    email_body: str,
    attachment_uris: str,
) -> str:
    """Parse supplier's response email and attachments into structured data.

    Handles plain text confirmations, PDF order acknowledgments,
    Excel line-item confirmations, and image/screenshot formats.
    Returns JSON with confirmed line items and any supplier notes.
    """
    import json
    from google import genai

    client = genai.Client()
    parts = [f"Supplier response email:\n{email_body}"]

    for uri in attachment_uris.split(","):
        uri = uri.strip()
        if not uri:
            continue
        # Detect MIME type from extension
        mime = "application/pdf"
        if uri.endswith((".xlsx", ".xls", ".csv")):
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif uri.endswith((".png", ".jpg", ".jpeg")):
            mime = "image/png"
        parts.append(genai.types.Part.from_uri(uri, mime_type=mime))

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=parts,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "confirmation_type": {
                        "type": "string",
                        "enum": ["full", "partial", "rejection", "counter_offer"],
                    },
                    "supplier_reference": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_description": {"type": "string"},
                                "supplier_sku": {"type": "string"},
                                "confirmed_quantity": {"type": "number"},
                                "confirmed_unit_price": {"type": "number"},
                                "confirmed_delivery_date": {"type": "string"},
                                "currency": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                        },
                    },
                    "supplier_notes": {"type": "string"},
                },
            },
        ),
    )
    return response.text


parse_response_tool = FunctionTool(func=parse_supplier_response)
```

```python
response_parser_agent = LlmAgent(
    name="ResponseParserAgent",
    model="gemini-2.5-pro",
    instruction="""You are a supplier response parser for purchase order confirmations.

Parse the supplier's email response using the parse_supplier_response tool.

Supplier email body: {supplier_email_body}
Attachment URIs: {supplier_attachment_uris}

After parsing, verify the extracted data makes sense:
- Are there line items? (A response with zero line items may be a
  general acknowledgment, not a line-level confirmation)
- Are quantities and prices numeric?
- Are dates parseable?

Output the parsed confirmation data. If the response is ambiguous or
appears to be a general inquiry rather than a PO confirmation, note this.
""",
    tools=[parse_response_tool],
    output_key="parsed_response",
)
```

**Design decisions:**
- Gemini Pro again for parsing, same rationale as Order Intake extraction. Supplier documents are diverse — handwritten notes, SAP screenshots, custom PDF layouts. Pro handles the long tail.
- The `response_schema` includes `confirmation_type` with an enum. This forces the LLM to classify the response upfront: full confirmation, partial (some items confirmed, others not), rejection, or counter-offer (supplier proposes different terms). This classification drives downstream behavior.
- The parser is a separate tool call, not the agent's native response. This gives explicit control over model version and schema enforcement for the extraction step, while the agent itself can reason about the quality of the extraction.

### Stage 2: POMatcherAgent

The matcher takes the parsed response and cross-references it against the original PO. This is the most critical stage — it produces the field-by-field comparison that determines whether the confirmation is clean or has discrepancies.

```python
def match_against_po(
    parsed_response_json: str,
    po_data_json: str,
) -> str:
    """Cross-reference supplier response against original PO.

    Performs line-by-line matching between confirmed items and PO items.
    For each line, compares quantity, price, delivery date, and item
    identification. Returns a match report with per-field status.
    """
    import json
    from datetime import datetime

    response = json.loads(parsed_response_json)
    po = json.loads(po_data_json)

    match_report = {"matches": [], "unmatched_po_lines": [], "unmatched_response_lines": []}
    po_lines = {i: line for i, line in enumerate(po.get("line_items", []))}
    matched_po_indices = set()

    for resp_item in response.get("line_items", []):
        best_match = None
        best_score = 0

        for idx, po_item in po_lines.items():
            if idx in matched_po_indices:
                continue
            # Simple matching: compare descriptions/SKUs
            score = 0
            if (resp_item.get("supplier_sku", "").lower() ==
                    po_item.get("supplier_sku", "").lower()):
                score += 3
            if resp_item.get("item_description", "").lower() in \
                    po_item.get("description", "").lower():
                score += 2
            if score > best_score:
                best_score = score
                best_match = (idx, po_item)

        if best_match:
            idx, po_item = best_match
            matched_po_indices.add(idx)

            qty_match = resp_item.get("confirmed_quantity") == po_item.get("quantity")
            price_match = abs(
                (resp_item.get("confirmed_unit_price", 0) - po_item.get("unit_price", 0))
                / max(po_item.get("unit_price", 1), 0.01)
            ) <= 0.02  # 2% tolerance

            # Date comparison
            resp_date = resp_item.get("confirmed_delivery_date", "")
            po_date = po_item.get("requested_delivery_date", "")
            date_match = resp_date == po_date  # Simplified; production uses date parsing

            match_report["matches"].append({
                "po_line_index": idx,
                "po_item": po_item,
                "confirmed_item": resp_item,
                "quantity_match": qty_match,
                "price_match": price_match,
                "date_match": date_match,
                "all_match": qty_match and price_match and date_match,
            })
        else:
            match_report["unmatched_response_lines"].append(resp_item)

    for idx, po_item in po_lines.items():
        if idx not in matched_po_indices:
            match_report["unmatched_po_lines"].append(po_item)

    return json.dumps(match_report)


match_po_tool = FunctionTool(func=match_against_po)
```

```python
po_matcher_agent = LlmAgent(
    name="POMatcherAgent",
    model="gemini-2.0-flash",
    instruction="""You are a PO matching engine for supplier confirmations.

Parsed supplier response: {parsed_response}
Original PO data: {po_data}

Use the match_against_po tool to cross-reference the supplier's confirmed
items against the original PO line items. Pass both as JSON strings.

After matching, summarize:
- How many PO lines were matched vs unmatched
- Which fields matched and which had discrepancies
- Whether there are lines in the supplier response that don't correspond
  to any PO line (possible supplier error or unsolicited items)

Output the full match report.
""",
    tools=[match_po_tool],
    output_key="match_report",
)
```

**Design decisions:**
- Gemini Flash for the matcher agent. The agent's job is to call a deterministic tool and summarize results — no multimodal capability needed.
- The matching logic is entirely in the `FunctionTool`, not the LLM. Price comparison with tolerance, quantity equality, date parsing — these are exact operations. The LLM summarizes; it does not compute.
- The 2% price tolerance is hardcoded here. In production, this comes from the SOP Playbook per supplier/commodity.

### Stage 3: DiscrepancyDetectorAgent

The detector takes the match report and produces a severity-ranked list of discrepancies with recommended actions.

```python
def detect_discrepancies(match_report_json: str) -> str:
    """Analyze match report and classify discrepancies by severity.

    Returns a list of discrepancies with severity (critical, major, minor),
    type (price, quantity, date, missing_line), and recommended action
    (auto_accept, clarify, escalate).
    """
    import json

    report = json.loads(match_report_json)
    discrepancies = []

    for match in report.get("matches", []):
        if match["all_match"]:
            continue

        po_item = match["po_item"]
        conf_item = match["confirmed_item"]

        if not match["quantity_match"]:
            po_qty = po_item.get("quantity", 0)
            conf_qty = conf_item.get("confirmed_quantity", 0)
            shortfall_pct = (po_qty - conf_qty) / max(po_qty, 1) * 100

            discrepancies.append({
                "type": "quantity",
                "po_line": match["po_line_index"],
                "expected": po_qty,
                "confirmed": conf_qty,
                "severity": "critical" if shortfall_pct > 20 else
                           "major" if shortfall_pct > 5 else "minor",
                "action": "escalate" if shortfall_pct > 20 else
                         "clarify" if shortfall_pct > 5 else "auto_accept",
                "detail": f"Quantity shortfall of {shortfall_pct:.1f}%",
            })

        if not match["price_match"]:
            po_price = po_item.get("unit_price", 0)
            conf_price = conf_item.get("confirmed_unit_price", 0)
            diff_pct = ((conf_price - po_price) / max(po_price, 0.01)) * 100

            discrepancies.append({
                "type": "price",
                "po_line": match["po_line_index"],
                "expected": po_price,
                "confirmed": conf_price,
                "severity": "critical" if abs(diff_pct) > 10 else
                           "major" if abs(diff_pct) > 2 else "minor",
                "action": "escalate" if abs(diff_pct) > 10 else
                         "clarify" if abs(diff_pct) > 2 else "auto_accept",
                "detail": f"Price difference of {diff_pct:+.1f}%",
            })

        if not match["date_match"]:
            discrepancies.append({
                "type": "delivery_date",
                "po_line": match["po_line_index"],
                "expected": po_item.get("requested_delivery_date"),
                "confirmed": conf_item.get("confirmed_delivery_date"),
                "severity": "major",
                "action": "clarify",
                "detail": "Delivery date mismatch",
            })

    # Missing PO lines — supplier didn't confirm some items
    for po_item in report.get("unmatched_po_lines", []):
        discrepancies.append({
            "type": "missing_confirmation",
            "po_line": po_item.get("description", "unknown"),
            "severity": "critical",
            "action": "clarify",
            "detail": "PO line not addressed in supplier response",
        })

    # Determine overall severity
    severities = [d["severity"] for d in discrepancies]
    overall = "clean" if not discrepancies else \
              "critical" if "critical" in severities else \
              "major" if "major" in severities else "minor"

    return json.dumps({
        "overall_severity": overall,
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
    })


detect_discrepancies_tool = FunctionTool(func=detect_discrepancies)
```

```python
discrepancy_detector_agent = LlmAgent(
    name="DiscrepancyDetectorAgent",
    model="gemini-2.0-flash",
    instruction="""You are a discrepancy analyzer for PO confirmations.

Match report: {match_report}

Use the detect_discrepancies tool to analyze the match report and
classify all discrepancies by severity and recommended action.

After detection, provide a human-readable summary:
- Overall status: clean, minor issues, major issues, or critical
- Count of discrepancies by type (price, quantity, date, missing)
- Recommended next action: auto_update_erp, send_clarification, or escalate_to_buyer

The recommended action is determined by the WORST discrepancy:
- All clean or only minor → auto_update_erp
- Any major → send_clarification
- Any critical → escalate_to_buyer
""",
    tools=[detect_discrepancies_tool],
    output_key="discrepancy_report",
)
```

### Stage 4: ActionAgent

The action agent executes the decision determined by the discrepancy report. It has three tools corresponding to the three paths: update ERP, send clarification to supplier, or escalate to buyer.

```python
def update_erp(po_id: str, confirmed_data_json: str) -> str:
    """Write confirmed PO data to Firestore (ERP staging layer).

    Updates the purchase order document with confirmed quantities, prices,
    and delivery dates from the supplier. Includes idempotency check.
    """
    import json
    from datetime import datetime, timezone
    from google.cloud import firestore

    db = firestore.Client()
    po_ref = db.collection("purchase_orders").document(po_id)

    # Idempotency: check if already confirmed
    po_doc = po_ref.get()
    if po_doc.exists and po_doc.to_dict().get("status") == "confirmed":
        return json.dumps({
            "status": "already_confirmed",
            "message": "PO was previously confirmed. No update needed.",
        })

    confirmed = json.loads(confirmed_data_json)
    po_ref.update({
        "status": "confirmed",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_line_items": confirmed.get("line_items", []),
        "confirmed_delivery_date": confirmed.get("delivery_date"),
        "confirmation_source": "ai_agent",
    })

    return json.dumps({"status": "erp_updated", "po_id": po_id})


def send_followup(
    supplier_email: str,
    po_number: str,
    discrepancy_summary: str,
    tone: str,
) -> str:
    """Send clarification or follow-up email to supplier.

    Composes and sends a professional email addressing specific
    discrepancies. The tone parameter controls formality level
    ('initial', 'reminder', 'urgent').
    """
    from googleapiclient.discovery import build
    import base64
    from email.mime.text import MIMEText

    subject_map = {
        "initial": f"Clarification Needed: PO {po_number}",
        "reminder": f"Follow-Up: PO {po_number} — Confirmation Requested",
        "urgent": f"Urgent: PO {po_number} — Response Required",
    }

    service = build("gmail", "v1")
    body = (
        f"Regarding PO {po_number}:\n\n"
        f"{discrepancy_summary}\n\n"
        "Could you please review and confirm the above?\n\n"
        "Best regards"
    )
    message = MIMEText(body)
    message["to"] = supplier_email
    message["subject"] = subject_map.get(tone, subject_map["initial"])
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return f'{{"status": "followup_sent", "tone": "{tone}"}}'


def escalate_to_buyer(
    po_id: str,
    buyer_email: str,
    discrepancy_report_json: str,
    recommended_action: str,
) -> str:
    """Escalate PO discrepancy to the responsible buyer.

    Creates an escalation task in Firestore and sends a notification
    email to the buyer with full context and AI-recommended action.
    """
    import json
    from datetime import datetime, timezone
    from google.cloud import firestore

    db = firestore.Client()
    report = json.loads(discrepancy_report_json)

    task = {
        "po_id": po_id,
        "discrepancies": report.get("discrepancies", []),
        "recommended_action": recommended_action,
        "status": "pending_buyer_action",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "buyer_email": buyer_email,
    }
    ref = db.collection("escalation_queue").add(task)

    # Update PO status
    db.collection("purchase_orders").document(po_id).update({
        "status": "escalated",
        "escalation_task_id": ref[1].id,
    })

    return json.dumps({
        "status": "escalated",
        "task_id": ref[1].id,
        "buyer_notified": buyer_email,
    })


update_erp_tool = FunctionTool(func=update_erp)
send_followup_tool = FunctionTool(func=send_followup)
escalate_to_buyer_tool = FunctionTool(func=escalate_to_buyer)
```

```python
action_agent = LlmAgent(
    name="ActionAgent",
    model="gemini-2.0-flash",
    instruction="""You are the action executor for PO Confirmation processing.

PO ID: {po_id}
PO data: {po_data}
Parsed supplier response: {parsed_response}
Match report: {match_report}
Discrepancy report: {discrepancy_report}

Based on the discrepancy report's recommended action, execute ONE path:

**AUTO-UPDATE ERP** (overall_severity is "clean" or "minor"):
1. Call update_erp with the PO ID and confirmed data from the parsed response.
2. The confirmation is complete. Output the ERP update result.

**SEND CLARIFICATION** (overall_severity is "major"):
1. Compose a specific, professional clarification message addressing each
   discrepancy. Reference exact line items, quantities, and prices.
   Example: "Line 3: We ordered 500 units at $12.50, but your confirmation
   shows 450 units at $12.80. Could you confirm the correct figures?"
2. Call send_followup with the supplier email, PO number, your composed
   message, and tone "initial".
3. Do NOT negotiate. State the facts and ask for confirmation.

**ESCALATE TO BUYER** (overall_severity is "critical"):
1. Compose a summary for the buyer: what was ordered, what the supplier
   confirmed, what the discrepancies are, and your recommended resolution.
2. Call escalate_to_buyer with the PO ID, buyer email, discrepancy report,
   and your recommended action.
3. Do NOT send any email to the supplier — the buyer will handle this.

IMPORTANT: You are NOT authorized to accept price increases, agree to
reduced quantities, or approve delivery delays. These are commercial
decisions for the buyer. Your job is to communicate facts accurately.
""",
    tools=[update_erp_tool, send_followup_tool, escalate_to_buyer_tool],
    output_key="action_result",
)
```

### The Follow-Up Agent (SLA Timer)

This agent runs independently from the main pipeline, triggered by Cloud Scheduler on a cron (every 4 hours is reasonable). It scans for POs past their SLA and sends follow-ups.

```python
followup_agent = LlmAgent(
    name="FollowupAgent",
    model="gemini-2.0-flash",
    instruction="""You are a follow-up agent for unconfirmed purchase orders.

PO data: {po_data}

This PO has not received a supplier confirmation within the SLA window.
Your job is to send a professional follow-up email.

Rules:
- First follow-up (followup_count = 0): Polite reminder. Tone: "reminder"
- Second follow-up (followup_count = 1): Firmer request. Tone: "reminder"
- Third follow-up (followup_count >= 2): Urgent. Tone: "urgent"
  Also escalate to the buyer because repeated non-response indicates
  a supplier relationship issue.

Current followup_count: {followup_count}
Supplier email: {supplier_email}
PO number: {po_number}

Compose a follow-up email that:
1. References the specific PO number and date
2. Lists key items and quantities (do not dump the entire PO)
3. Requests confirmation of delivery dates
4. Is written in clear, professional language that sounds human

Use send_followup to send the email. If this is the third+ follow-up,
also use escalate_to_buyer.
""",
    tools=[send_followup_tool, escalate_to_buyer_tool],
    output_key="followup_result",
)
```

The Cloud Scheduler handler that triggers follow-up checks:

```python
async def check_overdue_pos():
    """Triggered by Cloud Scheduler every 4 hours.

    Scans Firestore for POs in 'awaiting_response' status past SLA.
    For each overdue PO, invokes the followup_agent.
    """
    from google.cloud import firestore
    from datetime import datetime, timezone, timedelta

    db = firestore.Client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)  # Default SLA

    overdue_pos = (
        db.collection("purchase_orders")
        .where("status", "==", "awaiting_response")
        .where("last_sent_at", "<", cutoff)
        .stream()
    )

    for po_doc in overdue_pos:
        po_data = po_doc.to_dict()
        # Invoke followup_agent with PO context
        await handle_po_event(
            event_type="sla_check",
            po_id=po_doc.id,
        )
        # Update followup tracking
        db.collection("purchase_orders").document(po_doc.id).update({
            "followup_count": firestore.Increment(1),
            "last_sent_at": datetime.now(timezone.utc),
        })
```

### Callbacks: Idempotency and Audit

The PO Confirmation agent needs stronger callbacks than Order Intake because of its stateful nature. The same supplier email might be processed twice (Gmail Pub/Sub delivers at-least-once). The same PO might receive multiple follow-ups if the scheduler runs while a response is being processed.

```python
from typing import Optional, Dict, Any
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext


def idempotency_guard(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
) -> Optional[Dict]:
    """Prevent duplicate tool executions for the same PO + action.

    Checks Firestore for a recent identical action. If found,
    returns the cached result instead of re-executing.
    """
    from google.cloud import firestore
    from datetime import datetime, timezone, timedelta
    import hashlib
    import json

    db = firestore.Client()
    # Create a deterministic key from tool name + args
    action_key = hashlib.sha256(
        f"{tool.name}:{json.dumps(args, sort_keys=True)}".encode()
    ).hexdigest()[:16]

    # Check for recent duplicate (within 1 hour)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    existing = (
        db.collection("action_log")
        .where("action_key", "==", action_key)
        .where("timestamp", ">", cutoff)
        .limit(1)
        .get()
    )

    if existing:
        cached = existing[0].to_dict()
        return {"result": cached["result"], "note": "Deduplicated — action already taken"}

    return None  # Proceed with execution


def log_action(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict,
) -> Optional[Dict]:
    """Persist every tool action for audit and idempotency."""
    from google.cloud import firestore
    from datetime import datetime, timezone
    import hashlib
    import json

    db = firestore.Client()
    action_key = hashlib.sha256(
        f"{tool.name}:{json.dumps(args, sort_keys=True)}".encode()
    ).hexdigest()[:16]

    db.collection("action_log").add({
        "action_key": action_key,
        "agent": tool_context.agent_name,
        "tool": tool.name,
        "args": args,
        "result": str(tool_response)[:1000],
        "timestamp": datetime.now(timezone.utc),
    })
    return None


# Attach to all agents with side-effecting tools
for agent in [action_agent, followup_agent]:
    agent.before_tool_callback = idempotency_guard
    agent.after_tool_callback = log_action
```

### State Flow Summary (Response Processing Pipeline)

```
Initial state (set by Cloud Run handler):
  po_data, supplier_email_body, supplier_attachment_uris,
  supplier_email_id, po_id

ResponseParserAgent reads: supplier_email_body, supplier_attachment_uris
ResponseParserAgent writes: parsed_response
  → '{"confirmation_type": "partial", "line_items": [...]}'

POMatcherAgent reads: parsed_response, po_data
POMatcherAgent writes: match_report
  → '{"matches": [...], "unmatched_po_lines": [...]}'

DiscrepancyDetectorAgent reads: match_report
DiscrepancyDetectorAgent writes: discrepancy_report
  → '{"overall_severity": "major", "discrepancies": [...]}'

ActionAgent reads: po_id, po_data, parsed_response, match_report, discrepancy_report
ActionAgent writes: action_result
  → '{"action": "send_clarification", "status": "followup_sent"}'
```

## The Tradeoffs

**State machine outside ADK vs inside ADK.** The dispatcher is a plain Python function, not an ADK agent. An alternative design: use a single LlmAgent as the dispatcher, reading PO state and deciding which sub-pipeline to invoke via `sub_agents` and AutoFlow. This is more "ADK-native" but introduces LLM non-determinism into the routing decision. When the routing is deterministic (if state is X, do Y), using an LLM for it adds cost, latency, and unpredictability for zero benefit. The LLMs should handle what they are good at — parsing unstructured text, composing emails — and simple if/else should remain simple if/else.

**Four-agent pipeline vs two-agent pipeline.** You could collapse parser + matcher into one agent and detector + action into another. Two agents instead of four. Fewer LLM calls, lower cost. The tradeoff: when a PO match fails, you cannot tell if the parser extracted bad data or the matcher used the wrong algorithm. With four agents, you inspect `parsed_response` to verify extraction quality independently of matching quality. For a hackathon demo, two agents are fine. For production, four agents pay for themselves in debugging time within the first week.

**Cloud Scheduler for follow-ups vs event-driven timers.** Cloud Scheduler checks every 4 hours. This means a PO could sit unattended for up to 4 hours after its SLA expires. An event-driven alternative: when a PO is created, schedule a Cloud Tasks delayed message for exactly the SLA duration. When it fires, check if a response has arrived; if not, send the follow-up. This is more precise but more complex to manage (you need to cancel the task if a response arrives before the timer). For the hackathon, Cloud Scheduler is good enough. For production with hundreds of POs daily, Cloud Tasks gives tighter SLA compliance.

**Gemini Pro for parsing vs Flash.** The same tradeoff as Order Intake. Simple text confirmations ("Confirmed, will ship May 20") work fine with Flash. PDF order acknowledgments with complex table layouts need Pro. The cost-optimized path: use Flash by default, fall back to Pro if the Flash extraction returns incomplete data (e.g., zero line items from a PDF attachment). This requires a retry loop, which adds complexity but cuts cost by 60-80% on the easy cases.

## What Most People Get Wrong

**"The agent should track state internally."** ADK sessions are ephemeral by default (`InMemorySessionService`). If you rely on session state alone, a Cloud Run cold start or instance recycle loses everything. PO state must live in Firestore as the source of truth. Session state is a working scratchpad for the current invocation — Firestore is the persistent record. Every invocation starts by reading Firestore state into session state, and every invocation ends by writing results back to Firestore.

**"One pipeline handles all PO events."** No. The follow-up flow (SLA timer fires, compose email, send) is fundamentally different from the response processing flow (parse, match, detect, act). Forcing both through one SequentialAgent means the pipeline has dead stages on every invocation — the parser runs with no email on a follow-up, the follow-up logic runs with no timer on a response. Separate pipelines for separate triggers. The dispatcher pattern keeps each pipeline focused.

**"The clarification email should include a portal link."** This violates the [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design|Anti-Portal]] principle. The supplier replies by email. The clarification asks a specific question. The supplier replies to that email. The agent parses the reply. No portal needed. No login needed. No change in supplier behavior needed. The 94% acknowledgment rate at BraunAbility was achieved entirely through email, not portals.

**"Idempotency is optional for a demo."** It is not. Gmail Pub/Sub delivers at-least-once. Cloud Scheduler can double-fire on retries. Without idempotency guards, you will send duplicate follow-ups to suppliers, create duplicate escalation tasks, and write conflicting confirmations to the ERP. The `before_tool_callback` idempotency guard shown above adds ~20 lines of code and prevents an entire class of production bugs. Add it from day one.

## Connections

- [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] — Level 1 foundation with Glacis workflow, enterprise metrics, and the Anti-Portal rationale
- [[Glacis-Agent-Reverse-Engineering-Supplier-Communication]] — The supplier communication engine: tone calibration, follow-up escalation ladders, multi-language support
- [[Glacis-Agent-Reverse-Engineering-Exception-Handling]] — Exception routing patterns: what gets auto-resolved, clarified, or escalated
- [[Glacis-Agent-Reverse-Engineering-ADK-Order-Intake]] — Sibling implementation note for the Order Intake agent. Compare the pipeline patterns: single-pass vs state machine.
- [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]] — The collections (`purchase_orders`, `escalation_queue`, `action_log`) referenced by the FunctionTools
- [[Glacis-Agent-Reverse-Engineering-Event-Architecture]] — Pub/Sub topic design for Gmail notifications and Cloud Scheduler triggers
- [[Glacis-Agent-Reverse-Engineering-SOP-Playbook]] — Where the tolerance thresholds, SLA durations, and escalation rules are configured
- [[Glacis-Agent-Reverse-Engineering-Prompt-Templates]] — Production-grade prompts for supplier response parsing and follow-up composition
- [[Glacis-Agent-Reverse-Engineering-Overview]] — Full research map

## Subtopics for Further Deep Dive

| # | Subtopic | Why It Matters |
|---|----------|----------------|
| 1 | Firestore Data Model | The `purchase_orders` collection needs subcollections for events, a state machine field with Firestore rules preventing invalid transitions, and indexes for SLA queries. See [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]]. |
| 2 | Pub/Sub Event Architecture | Gmail Pub/Sub notifications need topic filters. Cloud Scheduler needs cron expressions. Both need dead-letter topics for failed processing. See [[Glacis-Agent-Reverse-Engineering-Event-Architecture]]. |
| 3 | Supplier Tone Calibration | The follow-up and clarification emails must sound human. This requires few-shot examples from real buyer emails, per-supplier tone preferences, and multi-language support. See [[Glacis-Agent-Reverse-Engineering-Supplier-Communication]]. |
| 4 | Error Recovery | What happens when update_erp fails because SAP is down? The agent needs retry with exponential backoff, circuit breaker, and manual recovery queue. See [[error-recovery-patterns]]. |

## References

- [ADK SequentialAgent Documentation](https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/) — Constructor parameters, execution model, state sharing
- [ADK Multi-Agent Systems](https://google.github.io/adk-docs/agents/multi-agents/) — output_key patterns, coordinator/dispatcher, generator-critic
- [ADK Callback Types](https://google.github.io/adk-docs/callbacks/types-of-callbacks/) — before_tool_callback, after_tool_callback signatures
- [Developer's Guide to Multi-Agent Patterns in ADK](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/) — Eight patterns including sequential, parallel, hierarchical, and human-in-the-loop
- [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] — Level 1 foundation with Glacis workflow, enterprise case studies, and design rationale
- [Glacis: AI For PO Confirmation V8](https://www.glacis.com/) — Primary source: Knorr-Bremse, WITTENSTEIN, IDEX, BraunAbility case studies
