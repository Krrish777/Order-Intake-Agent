---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Gemini Prompt Templates"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 4
date: 2026-04-08
tags:
  - research
  - supply-chain
  - prompt-engineering
  - gemini
  - structured-output
  - extraction
---

# Gemini Prompt Templates

> [!info] Context — Part of [[Glacis-Agent-Reverse-Engineering-Overview]]. Depth level: 4. Parent: [[Glacis-Agent-Reverse-Engineering-Generator-Judge]]

The previous notes designed the extraction pipeline, the validation architecture, and the Generator-Judge pattern. This note produces the actual prompts. Five prompt templates that cover the full lifecycle of an order from inbox to ERP: classify the email, extract order data, extract PO confirmation data, validate the extraction against business rules, and generate outbound communication. Each template includes the system instruction, the Pydantic schema that enforces structured output, and the Gemini API configuration that ties them together.

The design principle behind every prompt here: the model is a function. It receives typed input and produces typed output. Gemini's `response_schema` parameter accepts a Pydantic model and guarantees the response is syntactically valid JSON conforming to that schema. This eliminates the entire class of "LLM returned malformed JSON" errors. What remains is semantic validation -- does the extracted data make business sense? -- which is the job of the validation pipeline ([[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]]), not the prompt.

---

## Prompt 1: Email Classification

**Purpose**: Classify incoming email as order/confirmation/inquiry/followup/spam before any expensive extraction runs. This is the gate that prevents the extraction prompt from wasting tokens on irrelevant messages. Glacis's 5-stage pipeline lists classification as Stage 2, right after OCR transcription and before extraction. It costs fractions of a cent per call and prevents the extraction prompt from running on shipping inquiries, complaints, or spam.

**Model**: Gemini 2.5 Flash. Classification is a low-complexity task -- Flash handles it at 1/10th the cost of Pro with equivalent accuracy for categorical decisions. Temperature 0 because you want deterministic classification, not creative interpretation.

### Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional

class EmailClassification(BaseModel):
    """Classification of an incoming email to the orders inbox."""
    category: Literal["order", "po_confirmation", "inquiry", "followup", "spam"] = Field(
        description="Primary category. 'order' = customer placing a new order. "
                    "'po_confirmation' = supplier confirming/responding to a PO we sent. "
                    "'inquiry' = question about pricing, availability, or shipping. "
                    "'followup' = reference to an existing order or conversation. "
                    "'spam' = irrelevant, marketing, or misdirected."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How certain you are about the classification."
    )
    reasoning: str = Field(
        description="One sentence explaining why this category was chosen."
    )
    has_attachment: bool = Field(
        description="Whether the email has attachments that may contain order/PO data."
    )
    suggested_priority: Literal["urgent", "normal", "low"] = Field(
        description="Suggested processing priority based on content signals."
    )
```

### System Instruction

```
You are an email classifier for a supply chain order processing system.

Classify the incoming email into exactly one category:
- "order": Customer is placing a new order. Look for: line items, quantities, product references, delivery dates, PO numbers from the customer, ship-to addresses. An email that says "please find attached our PO" is an order.
- "po_confirmation": A supplier is responding to a purchase order WE sent. Look for: references to OUR PO number, confirmed quantities, confirmed delivery dates, price acknowledgments, order acknowledgment numbers.
- "inquiry": Questions about pricing, availability, lead times, product specs, or shipping status. No intent to place an order or confirm a PO.
- "followup": References an existing order or conversation. Includes amendments ("change the qty on PO-1234 to 500"), status requests ("where is my order?"), and cancellations.
- "spam": Marketing emails, newsletters, misdirected messages, out-of-office replies, automated delivery notifications from carriers.

Priority signals:
- "urgent": Contains words like "urgent," "ASAP," "rush," "expedite," or references a date within 3 business days.
- "normal": Standard order or confirmation with no urgency signals.
- "low": Inquiry, general followup, or spam.

If the email is ambiguous between "order" and "followup" (e.g., "same as last order but add 10 cases"), classify as "order" -- it requires the full extraction pipeline.

Return ONLY your classification. Do not extract order data. Do not summarize the email.
```

### API Call

```python
from google import genai
from google.genai import types

def classify_email(email_text: str, attachment_filenames: list[str]) -> EmailClassification:
    client = genai.Client()

    content = f"Email body:\n{email_text}\n\nAttachment filenames: {attachment_filenames}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=CLASSIFICATION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=EmailClassification,
            temperature=0.0,
        ),
    )
    return EmailClassification.model_validate_json(response.text)
```

**Cost**: ~100-200 input tokens per email. At Flash pricing, roughly $0.0001 per classification. Running this on 1,000 emails/day costs $0.10.

---

## Prompt 2: Order Extraction

**Purpose**: Extract structured order data from email body + attachments. This is the core extraction prompt that turns "Hey, need 200 units of Dark Roast 5lb by Friday to our Dallas warehouse" into a typed `OrderData` object. The prompt handles the 17+ field label variations documented in [[Glacis-Agent-Reverse-Engineering-Document-Processing]] and the multimodal input formats (email text, PDF, Excel, images).

**Model**: Gemini 2.5 Flash for standard documents. Escalate to Gemini 2.5 Pro for scanned/handwritten documents or documents where Flash extraction fails Pydantic validation on the first attempt. The tiered approach from [[Glacis-Agent-Reverse-Engineering-Document-Processing]] applies: deterministic extraction (openpyxl, csv.DictReader) handles structured formats before the LLM ever runs.

### Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import date

class OrderLineItem(BaseModel):
    """A single line item extracted from the order."""
    line_number: int = Field(description="Sequential line number starting from 1.")
    customer_description: str = Field(
        description="Exact product description as written by the customer. "
                    "Do NOT normalize or map to internal SKUs."
    )
    quantity: float = Field(gt=0, description="Numeric quantity ordered.")
    unit_of_measure: Optional[str] = Field(
        default=None,
        description="Unit: cases, pallets, each, kg, lbs, boxes, etc. "
                    "Null if not specified."
    )
    unit_price: Optional[float] = Field(
        default=None, ge=0,
        description="Price per unit if stated. Null if not in document."
    )
    requested_delivery_date: Optional[date] = Field(
        default=None,
        description="Delivery date if specified. Null if not in document."
    )
    line_confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in this line item's extraction accuracy."
    )
    extraction_notes: Optional[str] = Field(
        default=None,
        description="Notes on ambiguity, assumptions, or missing data for this line."
    )

class OrderData(BaseModel):
    """Complete order extracted from an email and its attachments."""
    customer_name: Optional[str] = Field(
        default=None, description="Customer or company name."
    )
    customer_po_number: Optional[str] = Field(
        default=None,
        description="Customer's PO or reference number. Not our internal order number."
    )
    line_items: list[OrderLineItem] = Field(
        min_length=1,
        description="All line items found in the order. Must have at least one."
    )
    ship_to_address: Optional[str] = Field(
        default=None,
        description="Full shipping address as written. Do not normalize."
    )
    bill_to_address: Optional[str] = Field(
        default=None,
        description="Billing address if different from ship-to. Null if not specified."
    )
    requested_delivery_date: Optional[date] = Field(
        default=None,
        description="Order-level delivery date, if not specified per line item."
    )
    special_instructions: Optional[str] = Field(
        default=None,
        description="Any special handling, shipping, or packaging instructions."
    )
    payment_terms: Optional[str] = Field(
        default=None,
        description="Payment terms if mentioned (Net 30, COD, etc.)."
    )
    overall_confidence: Literal["high", "medium", "low"] = Field(
        description="Overall confidence in the extraction. 'low' if any critical "
                    "field is uncertain or if document quality is poor."
    )
    source_format: Literal[
        "email_body", "pdf_digital", "pdf_scanned", "excel", "csv", "image", "mixed"
    ] = Field(description="Primary format the order data was extracted from.")
```

### System Instruction

```
You are a supply chain order extraction specialist. Extract ALL order data from the provided email and attachments into the specified JSON schema.

FIELD LABEL VARIATIONS — the document may use any of these labels:
- Quantity: Qty, QTY, Qty Ordered, Order Qty, Units, Pcs, Pieces, Count, Amount, No. of Units, EA, Each
- SKU/Product: Item #, Item No, Part Number, Part #, Material, Material No, Product Code, Catalog #, UPC, Description, Product, Article
- Price: Unit Price, Price/Unit, Rate, Cost, Amount, Ext Price, Extended, Price Each, $/Unit, Per Unit
- Delivery Date: Ship Date, Required Date, Need By, Deliver By, ETA, Due Date, Req Date, Wanted, Requested
- PO Number: PO #, Purchase Order, Order #, Order Number, Reference, Ref #, Your Ref, Our Order
- Address: Ship To, Deliver To, Destination, Consignee, Receiving, Drop Ship

EXTRACTION RULES:
1. Extract ONLY data visible in the document. Never infer values not present.
2. For customer_description, use the EXACT text from the document. Do not map to internal product codes.
3. If a field is absent, return null. Do not guess.
4. If a value is ambiguous (e.g., "12" could be quantity or price), use surrounding context to determine which. Note the ambiguity in extraction_notes.
5. If the email body and attachment contain conflicting data, prefer the attachment (formal document) over the email body. Note the conflict in extraction_notes.
6. For dates, interpret relative references ("next Friday," "end of month") based on the email's sent date.
7. If multiple ship-to addresses appear, extract the one associated with this specific order. Note others in special_instructions.

CONFIDENCE SCORING:
- "high": Value is clearly printed/typed, unambiguous, from a structured field.
- "medium": Value is readable but from free text, or requires minor interpretation.
- "low": Value is partially illegible, ambiguous, or inferred from context.
- overall_confidence is "low" if ANY line item has low confidence or if critical fields (customer_name, line_items) are uncertain.

FEW-SHOT EXAMPLE:

Input email: "Hi team, please process the attached PO. Note: need the dark roast shipped separately from the blends. Thanks, Maria at Pacific Coast Roasters"
Attached PDF contains a table:
| Item | Qty | Unit Price | Delivery |
|------|-----|-----------|----------|
| Dark Roast 5lb bag | 200 | $14.50 | April 15 |
| Breakfast Blend 12oz | 150 | $8.75 | April 15 |
PO #: PCR-2026-0412

Expected output:
{
  "customer_name": "Pacific Coast Roasters",
  "customer_po_number": "PCR-2026-0412",
  "line_items": [
    {
      "line_number": 1,
      "customer_description": "Dark Roast 5lb bag",
      "quantity": 200,
      "unit_of_measure": null,
      "unit_price": 14.50,
      "requested_delivery_date": "2026-04-15",
      "line_confidence": "high",
      "extraction_notes": null
    },
    {
      "line_number": 2,
      "customer_description": "Breakfast Blend 12oz",
      "quantity": 150,
      "unit_of_measure": null,
      "unit_price": 8.75,
      "requested_delivery_date": "2026-04-15",
      "line_confidence": "high",
      "extraction_notes": null
    }
  ],
  "ship_to_address": null,
  "bill_to_address": null,
  "requested_delivery_date": "2026-04-15",
  "special_instructions": "Dark roast shipped separately from the blends",
  "payment_terms": null,
  "overall_confidence": "high",
  "source_format": "mixed"
}

Note: customer_description is "Dark Roast 5lb bag" — the exact text from the PDF, NOT an internal SKU. The special instruction from the email body ("shipped separately") is captured even though it was not in the PDF attachment.
```

### API Call

```python
def extract_order(
    email_text: str,
    attachments: list[tuple[bytes, str]],  # (content, mime_type)
) -> OrderData:
    client = genai.Client()

    parts = [types.Part.from_text(f"Email body:\n{email_text}")]
    for content_bytes, mime_type in attachments:
        parts.append(types.Part.from_bytes(data=content_bytes, mime_type=mime_type))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=ORDER_EXTRACTION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=OrderData,
            temperature=0.1,
        ),
    )
    return OrderData.model_validate_json(response.text)
```

**Temperature 0.1**: Low temperature for extraction. The model should report what it sees, not invent. Higher temperature increases hallucination risk on field values -- exactly the failure mode Alan Engineering documented (see [[Glacis-Agent-Reverse-Engineering-Document-Processing]]).

**Cost**: ~500-1500 input tokens per document (text + image). A 3-page PDF costs roughly $0.002-0.005 with Flash pricing.

---

## Prompt 3: PO Confirmation Extraction

**Purpose**: Extract supplier response data from a PO confirmation email and match it against the original PO we sent. This is the PO Confirmation Agent's core extraction -- the supplier responded to our purchase order, and we need to know what they confirmed, what they changed, and what they ignored. The schema is structured around deltas: confirmed items match the original PO, price changes and date changes capture discrepancies.

**Model**: Gemini 2.5 Flash. Same rationale as order extraction. The key difference: this prompt receives the original PO data as context alongside the supplier's response, so the model can compute deltas directly.

### Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import date

class ConfirmedItem(BaseModel):
    """A line item the supplier confirmed."""
    po_line_number: int = Field(description="Line number from the original PO.")
    supplier_item_reference: Optional[str] = Field(
        default=None,
        description="Supplier's own part/reference number if provided."
    )
    confirmed_quantity: float = Field(
        gt=0, description="Quantity the supplier confirmed."
    )
    confirmed_unit_price: Optional[float] = Field(
        default=None, ge=0,
        description="Price the supplier confirmed. Null if not stated."
    )
    confirmed_delivery_date: Optional[date] = Field(
        default=None,
        description="Delivery date the supplier confirmed. Null if not stated."
    )
    status: Literal["confirmed", "partial", "backordered", "rejected"] = Field(
        description="'confirmed' = full match. 'partial' = quantity differs. "
                    "'backordered' = supplier acknowledges but delays. "
                    "'rejected' = supplier cannot fulfill this line."
    )
    supplier_notes: Optional[str] = Field(
        default=None,
        description="Any notes the supplier added for this line item."
    )

class PriceChange(BaseModel):
    """A price discrepancy between original PO and supplier confirmation."""
    po_line_number: int
    original_price: float = Field(description="Price on our original PO.")
    confirmed_price: float = Field(description="Price the supplier stated.")
    delta_percent: float = Field(
        description="Percentage change: ((confirmed - original) / original) * 100"
    )
    within_tolerance: Optional[bool] = Field(
        default=None,
        description="Whether delta is within the configured tolerance band. "
                    "Null if tolerance is unknown to the model."
    )

class DateChange(BaseModel):
    """A delivery date discrepancy between original PO and supplier confirmation."""
    po_line_number: int
    original_date: date = Field(description="Date on our original PO.")
    confirmed_date: date = Field(description="Date the supplier stated.")
    delta_days: int = Field(
        description="Days of difference. Positive = supplier is later than requested."
    )

class ConfirmationData(BaseModel):
    """Structured extraction of a supplier's PO confirmation response."""
    supplier_name: Optional[str] = Field(
        default=None, description="Supplier company name."
    )
    our_po_number: Optional[str] = Field(
        default=None,
        description="Our PO number that the supplier is responding to."
    )
    supplier_reference_number: Optional[str] = Field(
        default=None,
        description="Supplier's own order/confirmation reference number."
    )
    confirmation_date: Optional[date] = Field(
        default=None,
        description="Date the supplier sent this confirmation."
    )
    confirmed_items: list[ConfirmedItem] = Field(
        description="All line items the supplier addressed in their response."
    )
    price_changes: list[PriceChange] = Field(
        default_factory=list,
        description="Line items where the supplier's price differs from our PO."
    )
    date_changes: list[DateChange] = Field(
        default_factory=list,
        description="Line items where the supplier's date differs from our PO."
    )
    unaddressed_lines: list[int] = Field(
        default_factory=list,
        description="PO line numbers the supplier did NOT address in their response."
    )
    overall_status: Literal[
        "fully_confirmed", "partial_confirmation", "has_exceptions", "rejected"
    ] = Field(
        description="'fully_confirmed' = all lines confirmed at PO terms. "
                    "'partial_confirmation' = some lines confirmed, some not addressed. "
                    "'has_exceptions' = confirmed but with price/date changes. "
                    "'rejected' = supplier declined the entire PO."
    )
    overall_confidence: Literal["high", "medium", "low"]
    extraction_notes: Optional[str] = Field(
        default=None,
        description="Any ambiguities, conflicts, or assumptions made during extraction."
    )
```

### System Instruction

```
You are a supply chain PO confirmation extraction specialist. You will receive TWO inputs:
1. The ORIGINAL PURCHASE ORDER we sent to the supplier (structured data).
2. The SUPPLIER'S RESPONSE (email body and/or attachments).

Your job: extract what the supplier confirmed, identify discrepancies, and flag unaddressed items.

MATCHING RULES:
- Match supplier response lines to original PO lines by: item description similarity, part number, line number references, or positional order if no explicit reference exists.
- If the supplier confirms "all items as per your PO" without listing specifics, mark all lines as confirmed with the original PO values. Note this interpretation in extraction_notes.
- If the supplier references a line we did not send, add it with extraction_notes explaining the mismatch.

PRICE CHANGE DETECTION:
- Calculate delta_percent as ((confirmed_price - original_price) / original_price) * 100.
- Even small changes matter. A $0.03 difference on 10,000 units is $300. Always report.
- If no price is mentioned in the confirmation, do NOT populate a PriceChange entry. The absence of a price statement is not a price change.

DATE CHANGE DETECTION:
- Calculate delta_days as confirmed_date minus original_date. Positive = supplier is later.
- "Delivery in week 16" means the Monday of ISO week 16 of the relevant year.
- "4-6 weeks" from the confirmation date means use the LATER date (6 weeks) as confirmed_date.

UNADDRESSED LINES:
- If the original PO has 5 lines but the supplier only mentions 3, the other 2 go in unaddressed_lines. This is critical -- unaddressed lines are the most common source of PO exceptions. The PO Confirmation Agent uses this to trigger automated follow-up (see the Supplier Communication Engine).

EXTRACTION RULES:
1. Extract ONLY data present in the supplier's response. Never copy values from the original PO into confirmed fields unless the supplier explicitly states them.
2. If the supplier says "confirmed" without specifying a price, confirmed_unit_price is null -- not the original PO price. We cannot assume silence means agreement on price.
3. Return overall_status based on the aggregate: all confirmed at original terms = "fully_confirmed". Any price/date changes = "has_exceptions". Any unaddressed lines = "partial_confirmation". Explicit rejection = "rejected".
```

### API Call

```python
def extract_confirmation(
    original_po: dict,  # Structured PO data from Firestore
    supplier_email: str,
    attachments: list[tuple[bytes, str]],
) -> ConfirmationData:
    client = genai.Client()

    context = f"ORIGINAL PURCHASE ORDER:\n{json.dumps(original_po, indent=2, default=str)}"
    parts = [
        types.Part.from_text(context),
        types.Part.from_text(f"\nSUPPLIER RESPONSE EMAIL:\n{supplier_email}"),
    ]
    for content_bytes, mime_type in attachments:
        parts.append(types.Part.from_bytes(data=content_bytes, mime_type=mime_type))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=PO_CONFIRMATION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ConfirmationData,
            temperature=0.1,
        ),
    )
    return ConfirmationData.model_validate_json(response.text)
```

---

## Prompt 4: Validation Judgment (Generator-Judge Pattern)

**Purpose**: The Judge in the Generator-Judge pattern. The Generator (Prompts 2 or 3) extracted data. The Judge evaluates that extraction against business rules and decides whether it can proceed automatically or needs human review. This is the quality gate described in [[Glacis-Agent-Reverse-Engineering-Exception-Handling]] -- the pre-execution validation that prevents confidently wrong agents from taking irreversible action.

**Model**: Gemini 2.5 Flash. The Judge does not need the extraction capability of Pro -- it needs fast, deterministic rule evaluation. Temperature 0 because judgment should not vary between runs for the same input.

### Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class FieldValidation(BaseModel):
    """Validation result for a single field."""
    field_name: str = Field(description="Name of the field being validated.")
    status: Literal["pass", "fail", "warning"] = Field(
        description="'pass' = field is valid. 'fail' = field violates a business rule. "
                    "'warning' = field is technically valid but unusual."
    )
    rule_checked: str = Field(
        description="The business rule that was evaluated."
    )
    details: Optional[str] = Field(
        default=None,
        description="Explanation of why this field passed, failed, or triggered a warning."
    )
    suggested_correction: Optional[str] = Field(
        default=None,
        description="If status is 'fail' or 'warning', what the correct value might be."
    )

class ValidationResult(BaseModel):
    """Complete validation judgment for an extracted order or confirmation."""
    field_validations: list[FieldValidation] = Field(
        description="Validation result for each checked field."
    )
    critical_failures: int = Field(
        ge=0,
        description="Count of fields with status 'fail'. Zero means all rules pass."
    )
    warnings: int = Field(
        ge=0,
        description="Count of fields with status 'warning'."
    )
    overall_confidence: Literal["high", "medium", "low"] = Field(
        description="Aggregate confidence. 'high' = 0 failures, <=1 warning. "
                    "'medium' = 0 failures but 2+ warnings. "
                    "'low' = any failures."
    )
    recommended_action: Literal[
        "auto_execute", "human_review", "request_clarification", "reject"
    ] = Field(
        description="'auto_execute' = safe to process without human intervention. "
                    "'human_review' = present to operator with flags. "
                    "'request_clarification' = send clarification email to sender. "
                    "'reject' = data is unusable, log and notify."
    )
    action_reasoning: str = Field(
        description="One to two sentences explaining why this action was recommended."
    )
```

### System Instruction

```
You are a validation judge for a supply chain order processing system. You receive:
1. EXTRACTED DATA: The output of an AI extraction (order or PO confirmation).
2. BUSINESS RULES: The active rules for this customer/supplier.
3. MASTER DATA: Relevant product, price, and customer records.

Your job: evaluate every field in the extracted data against the business rules and master data. You are the quality gate. Nothing proceeds to the ERP without your approval.

VALIDATION RULES TO CHECK:

Quantity rules:
- quantity > 0 (always)
- quantity <= max_order_quantity for this product (if provided)
- quantity is a whole number for discrete items (you cannot order 2.5 motors)
- quantity is within 3x of the customer's historical average for this product (flag outliers)

Price rules:
- unit_price > 0 (always)
- unit_price is within the configured tolerance band of the contract/list price
- If no price was extracted (null), this is NOT a failure -- it means the customer expects contracted pricing

Date rules:
- requested_delivery_date is in the future
- requested_delivery_date allows for minimum lead time (if lead time data is provided)
- requested_delivery_date is not a weekend or holiday (if calendar data is provided)

Address rules:
- ship_to_address is not null for orders requiring delivery
- ship_to_address bears reasonable similarity to a known address in customer master data

Consistency rules:
- customer_po_number is not a duplicate of a recently processed PO
- line_items has at least one entry
- If the email was classified as "order" but no line items were extracted, this is a critical failure

PO Confirmation-specific rules:
- unaddressed_lines should trigger "request_clarification" -- the supplier needs to confirm all lines
- price_changes exceeding the tolerance band trigger "human_review"
- date_changes exceeding the buffer window trigger "human_review"

JUDGMENT PROTOCOL:
1. Evaluate each field independently. A passing price does not compensate for a failing SKU.
2. Be conservative. When in doubt, recommend "human_review" over "auto_execute". A false escalation wastes 30 seconds of human time. A false auto-execution creates a downstream exception that costs hours.
3. Explain your reasoning. The human reviewer will see your field_validations and action_reasoning. Make them useful.
4. Never modify the extracted data. You judge -- you do not correct. Corrections happen in the next pipeline stage.
```

### API Call

```python
def validate_extraction(
    extracted_data: dict,
    business_rules: dict,
    master_data: dict,
) -> ValidationResult:
    client = genai.Client()

    context = (
        f"EXTRACTED DATA:\n{json.dumps(extracted_data, indent=2, default=str)}\n\n"
        f"BUSINESS RULES:\n{json.dumps(business_rules, indent=2)}\n\n"
        f"MASTER DATA:\n{json.dumps(master_data, indent=2, default=str)}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=context,
        config=types.GenerateContentConfig(
            system_instruction=VALIDATION_JUDGE_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ValidationResult,
            temperature=0.0,
        ),
    )
    return ValidationResult.model_validate_json(response.text)
```

**Temperature 0.0**: The Judge must be deterministic. Same input, same judgment, every time. If two runs of the Judge disagree on the same data, you have a reliability problem that no amount of threshold tuning can fix.

---

## Prompt 5: Communication Generation

**Purpose**: Generate outbound emails -- supplier follow-ups for unconfirmed POs, customer clarification requests for ambiguous orders, and order acknowledgment confirmations. This prompt connects to the [[Glacis-Agent-Reverse-Engineering-Supplier-Communication]] engine. The critical constraint: the LLM generates text that goes to external parties (customers and suppliers). Hallucinated URLs, false delivery promises, or off-brand language are not extraction errors that stay internal -- they damage business relationships.

**Model**: Gemini 2.5 Flash. Communication generation is not a complex reasoning task -- it is a constrained text generation task. Flash is fast and cheap. The safety constraints come from the system prompt, not model capability.

### Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional

class GeneratedEmail(BaseModel):
    """A professionally generated outbound email."""
    subject: str = Field(
        max_length=120,
        description="Email subject line. Include relevant PO/order reference numbers."
    )
    body: str = Field(
        description="Email body text. Professional tone. No markdown formatting -- "
                    "this goes into a plain-text or HTML email template."
    )
    tone: Literal["formal", "friendly", "urgent"] = Field(
        description="The tone used in the generated email."
    )
    contains_commitment: bool = Field(
        description="True if the email makes any promise about dates, prices, "
                    "quantities, or actions. These MUST be verified before sending."
    )
    commitment_details: Optional[str] = Field(
        default=None,
        description="If contains_commitment is true, list every specific promise made "
                    "so a human can verify before sending."
    )
    requires_human_review: bool = Field(
        description="True if the email should be reviewed by a human before sending. "
                    "Always true for: first contact with a new supplier, emails "
                    "containing commitments, emails about disputes or rejections."
    )
```

### System Instruction

```
You are a professional supply chain communication specialist. Generate emails that are sent to customers and suppliers on behalf of the procurement/customer service team.

COMMUNICATION TYPES:
1. SUPPLIER FOLLOW-UP: Remind a supplier about an unconfirmed PO. Be professional, specific (reference the PO number, line items, and original dates), and direct. Do not be apologetic -- we are the customer.
2. CUSTOMER CLARIFICATION: Ask a customer to clarify something about their order. Be helpful, specific about what is missing, and provide options when possible. Do not ask them to "resubmit" the entire order.
3. ORDER ACKNOWLEDGMENT: Confirm to the customer that their order has been received and is being processed. Include the key details (PO number, items, expected timeline) as confirmation.
4. EXCEPTION NOTIFICATION: Notify a buyer/coordinator about an exception that requires their attention. Internal email -- can be more direct and technical.

SAFETY CONSTRAINTS — MANDATORY:
- NEVER include URLs. Not links to portals, not links to tracking pages, not links to documents. URLs in generated emails are hallucination vectors. If a URL is needed, the email template system adds it after generation.
- NEVER promise a specific delivery date unless it is provided in the context data as a confirmed date. Use "we will confirm delivery timing shortly" for unconfirmed dates.
- NEVER state a price unless it is provided in the context data. Do not reference "your contracted rate" unless the actual rate is in the context.
- NEVER mention competitor names, internal system names, or AI/automation. The email should read as if written by a human team member.
- NEVER use superlatives ("best," "fastest," "guaranteed") or marketing language.
- NEVER apologize preemptively. "Sorry for any inconvenience" is filler. Be direct.

TONE GUIDELINES:
- "formal": For new relationships, large orders, and dispute resolution. Full sentences, proper salutations, sign-off with team name.
- "friendly": For established relationships and routine communications. Can use first names, shorter sentences, warmer sign-off.
- "urgent": For SLA breaches, production-critical parts, and time-sensitive exceptions. Direct, action-oriented, clear deadlines.

STRUCTURE:
1. Opening: Reference the specific PO/order number immediately. No "I hope this email finds you well."
2. Body: State exactly what is needed. If asking for information, list the specific fields. If confirming, list the key data points.
3. Close: Clear next step or expected response timeline. Sign off with the team name (provided in context), not a personal name.

Set requires_human_review to true for: first contact with new suppliers, any email containing price or date commitments, dispute/rejection emails, and any communication where the context data has low confidence scores.
```

### API Call

```python
def generate_communication(
    comm_type: Literal[
        "supplier_followup", "customer_clarification",
        "order_acknowledgment", "exception_notification"
    ],
    context_data: dict,
    sender_team: str,
    tone: Literal["formal", "friendly", "urgent"] = "friendly",
) -> GeneratedEmail:
    client = genai.Client()

    content = (
        f"COMMUNICATION TYPE: {comm_type}\n"
        f"SENDER TEAM: {sender_team}\n"
        f"REQUESTED TONE: {tone}\n\n"
        f"CONTEXT DATA:\n{json.dumps(context_data, indent=2, default=str)}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=COMMUNICATION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=GeneratedEmail,
            temperature=0.4,
        ),
    )
    return GeneratedEmail.model_validate_json(response.text)
```

**Temperature 0.4**: Higher than extraction (0.1) because you want natural-sounding variation in professional communication. Not so high that it gets creative with facts. The safety constraints in the system prompt are the real guardrail, not the temperature.

---

## Key Gemini Features Across All Prompts

Three API features make these prompts production-grade rather than prototype-grade:

**`response_mime_type: "application/json"`** -- Tells Gemini to output valid JSON. Without this, the model might wrap JSON in markdown code blocks, add explanatory text before/after the JSON, or produce other formats.

**`response_schema` from Pydantic** -- Accepts a Pydantic BaseModel and constrains the output to match that schema. Fields with `Literal[...]` types become enum constraints in the JSON Schema. Optional fields can be null. Lists enforce `min_length`. This eliminates parse failures entirely. The Gemini API converts the Pydantic model to JSON Schema internally via `model_json_schema()`, and the model's output is guaranteed to validate against it. As Google's structured output documentation states: "the model generates responses adhering to this schema."

**`temperature` control per task** -- Classification and validation get 0.0 (deterministic). Extraction gets 0.1 (near-deterministic, tiny variation to avoid degenerate outputs). Communication gets 0.4 (natural variation within safety constraints). This is not arbitrary -- it maps directly to the cost of error. A classification error wastes an extraction call ($0.005). An extraction hallucination creates a wrong order in the ERP ($50-500 in downstream costs). A communication error damages a business relationship (unquantifiable).

---

## The Prompt Engineering Principles

These five prompts share design patterns that apply to any supply chain LLM integration:

**Separate extraction from enrichment.** The extraction prompts (2, 3) return only what the document says. The validation prompt (4) checks against business rules. The communication prompt (5) generates from verified data. They never cross-contaminate. This is the lesson from Alan Engineering's few-shot contamination failure documented in [[Glacis-Agent-Reverse-Engineering-Document-Processing]].

**Schemas are the contract.** The Pydantic models are not just output formatters -- they are the API contract between pipeline stages. The extraction output schema is the validation input schema. The validation output schema drives the routing decision. Changing a field name in the schema is a breaking change that propagates through the entire pipeline. Treat schemas like database migrations: version them, review them, test them.

**Anti-hallucination is structural, not instructional.** "Do not hallucinate" in a prompt is wishful thinking. Structural anti-hallucination means: required fields that force the model to admit gaps (null instead of guessing), confidence enums that quantify uncertainty, separation of extraction from enrichment so the model cannot learn values from training examples that did not come from the document, and a Judge that independently validates output before it reaches the ERP.

**Temperature maps to error cost.** This is the simplest heuristic that most teams get wrong. If a wrong answer costs $0.001 (misclassification), temperature can be higher. If a wrong answer costs $500 (wrong order in ERP), temperature should be near zero. If a wrong answer damages a relationship (bad email to a supplier), temperature should be moderate -- natural enough to sound human, constrained enough to stay safe.

---

## Connections

- **Parent**: [[Glacis-Agent-Reverse-Engineering-Generator-Judge]] -- the Generator-Judge pattern these prompts instantiate. Prompts 2/3 are Generators. Prompt 4 is the Judge.
- **Extraction pipeline**: [[Glacis-Agent-Reverse-Engineering-Document-Processing]] -- the tiered extraction architecture that determines when these prompts run (only Tier 2+ documents hit the LLM).
- **Token optimization**: [[Glacis-Agent-Reverse-Engineering-Token-Optimization]] -- cost control strategies for running these prompts at scale. The tiered approach, Flash vs Pro selection, and input token minimization all apply.
- **Overview**: [[Glacis-Agent-Reverse-Engineering-Overview]] -- the full research map placing these prompts at Level 4 (Build-Level Detail).

---

## References

- [Gemini Structured Output Documentation](https://ai.google.dev/gemini-api/docs/structured-output) -- response_mime_type, response_schema, JSON Schema support, Pydantic integration
- [Gemini Prompt Design Strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies) -- Few-shot examples, system instructions, task decomposition
- [How to Use Gemini Structured Output and JSON Mode for Reliable Data Extraction](https://oneuptime.com/blog/post/2026-02-17-how-to-use-gemini-structured-output-and-json-mode-for-reliable-data-extraction/view) -- JSON Mode vs Structured Output, enum constraints, batch processing patterns
- [Google Blog: Improving Structured Outputs in the Gemini API](https://blog.google/technology/developers/gemini-api-structured-outputs/) -- anyOf, $ref support, key ordering preservation
- Glacis, "How AI Automates Order Intake in Supply Chain" (Dec 2025) -- 17+ field label variations, extraction accuracy requirements
- Glacis, "AI For PO Confirmation V8" (March 2026) -- PO cross-reference matching, unaddressed line detection
- Alan Engineering Blog, "5-Stage Document Extraction Pipeline" (March 2026) -- Few-shot contamination warning, separation of extraction from enrichment
- Pallet, "Deep Reasoning in AI Agents" -- Generator-Judge pattern for logistics validation
