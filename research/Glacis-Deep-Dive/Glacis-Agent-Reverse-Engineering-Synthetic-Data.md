---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Synthetic Data and Test Fixtures"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 5
date: 2026-04-08
tags:
  - research
  - supply-chain
  - synthetic-data
  - test-fixtures
  - kaggle
  - demo-data
---

# Synthetic Data and Test Fixtures

> [!info] Context
> Depth level: 5. Parent: [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]. Siblings: [[Glacis-Agent-Reverse-Engineering-Build-Plan]]

## The Problem

The demo scenario in [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]] defines what the audience sees. This note defines what the system chews on underneath.

You cannot demo an order intake agent without orders. You cannot demo a PO confirmation agent without purchase orders, supplier profiles, and confirmation emails. You cannot show exception handling without exceptions baked into the data. And you cannot use real procurement data --- it contains customer names, negotiated prices, supplier contracts, and order volumes that no company will share with a hackathon team.

The data must be synthetic but not random. Random data produces flat, boring demos. "Order #4827 from Customer XYZ for 100 units of Product ABC at $10.00" tells no story. Realistic synthetic data produces drama: a customer who always orders the same 5 SKUs suddenly adds a new one. A supplier who has been reliable for 6 months misses a delivery window. A price that has been stable for a year jumps 5.7% because raw material costs shifted. These patterns are what make the demo feel real, and they must be designed, not generated randomly.

The additional constraint is volume. A hackathon demo needs enough data to look credible but not so much that generation and seeding becomes a project in itself. The Smart Exception Triage research (see [[Supply-Chain-Seed-Data-Generation]]) already solved this sizing problem for a different demo: 40 suppliers, 150 products, 400 orders. This note adapts that principle for the order intake and PO confirmation use case, where the entity types are different and the required formats extend beyond database records into emails, PDFs, and spreadsheets.

## First Principles

Synthetic data for a demo serves three masters simultaneously, and confusing them produces data that fails at all three.

**Master 1: The demo narrative.** The three supplier types in the demo scenario --- Responsive (Mehta Industrial), Delayed (Sharma Steel), Problematic (Gupta Polymers) --- each require specific data properties. Mehta's order must match the product catalog exactly. Sharma's PO confirmation must contain a delivery date discrepancy within tolerance. Gupta's must contain a price increase above tolerance from a sole-source supplier with historical context. These are not random --- they are scripted fixtures that must exist exactly as specified.

**Master 2: The system pipeline.** Beyond the three demo scenarios, the agent pipeline needs data to warm up and operate realistically. The product catalog must have enough entries that SKU matching is non-trivial. The supplier list must have enough variety that the follow-up automation has realistic SLA distributions. The order history must be large enough that the learning loop has precedents to retrieve. This is background data --- the audience never sees individual records, but the system needs them to function.

**Master 3: Testing and development.** Before the demo, every pipeline stage needs test cases covering edge conditions. What happens when an email has no subject line? When a PDF attachment is a scanned image, not a text PDF? When two customers send orders with the same PO number? When a supplier confirms a PO that does not exist in the system? These test fixtures exercise failure paths that the demo may never show but that must work correctly for the system to be reliable.

The data generation strategy must produce all three categories --- scripted demo fixtures, realistic background data, and edge-case test fixtures --- from a coherent underlying model. The product catalog is shared across all three. The supplier profiles are shared. The pricing model is shared. Generate the foundation once, then layer the specific scenarios on top.

## Data Architecture

### Entity 1: Product Catalog (80 products)

The product catalog is the foundation. Every order line item, every PO, and every confirmation references products from this catalog. The catalog must be large enough that the demo's products are not obviously cherry-picked, but small enough that generation and review take less than an hour.

**Why 80 products:** The demo uses 3-4 products explicitly. The validation pipeline needs a realistic search space for SKU matching --- if the catalog has 5 products, exact matching is trivially easy and unimpressive. 80 products across 4-5 categories creates realistic ambiguity (multiple products with similar descriptions) without becoming unwieldy. Kaggle's DataCo Smart Supply Chain dataset uses similar scales for demonstration purposes.

**Schema:**

```python
product = {
    "sku": "HA-2041",                    # Internal SKU code
    "name": "Hydraulic Actuator, Grade A, 50mm",
    "category": "hydraulic_components",   # One of 5 categories
    "aliases": [                          # Customer-facing names that differ from internal
        "Grade-A hydraulic actuator",
        "50mm actuator",
        "HA actuator grade A"
    ],
    "unit_price": 12.50,                  # Catalog price (USD)
    "unit_of_measure": "each",
    "min_order_qty": 10,
    "lead_time_days": 5,
    "weight_kg": 3.2,
    "suppliers": ["SUP-001", "SUP-003"],  # Which suppliers provide this
    "sole_source": False,                 # Critical for escalation logic
    "price_history": [                    # For context retrieval in exceptions
        {"date": "2025-10-01", "price": 11.75},
        {"date": "2026-01-15", "price": 12.50}
    ]
}
```

**Five product categories (Indian manufacturing context):**

| Category | Products | Example SKUs | Why This Category |
|----------|----------|-------------|-------------------|
| Hydraulic Components | 20 | HA-2041, HA-3022 | Core demo products (Mehta order) |
| Steel & Metal Stock | 15 | SS-1001, SS-1042 | Sharma Steel's domain |
| Polymer & Resin | 15 | PR-5010, PR-5028 | Gupta Polymers' domain, sole-source items |
| Electrical Components | 15 | EC-3001, EC-3019 | Background variety |
| Packaging Materials | 15 | PM-4001, PM-4012 | Low-value, high-volume items for contrast |

**Alias generation strategy:** Each product gets 3-5 aliases that mimic how real customers refer to items. The core challenge of order intake is mapping "Dark Roast 5lb bag" to `SKU-4492`. Use Gemini to generate realistic aliases: abbreviations ("hyd actuator 50mm"), informal names ("the big actuator"), unit conversions ("2-inch actuator" for a 50mm part), brand-specific names, and misspellings ("hydralic actuater"). This is where LLM-generated synthetic data genuinely adds value over Faker.

### Entity 2: Customer List (15 customers)

**Schema:**

```python
customer = {
    "id": "CUST-001",
    "name": "Mehta Industrial Supplies",
    "contact_name": "Rajesh Mehta",
    "contact_email": "rajesh@mehtaindustrial.com",
    "tier": "enterprise",             # enterprise | mid-market | small
    "credit_limit": 50000,
    "outstanding_ar": 47100,          # Current accounts receivable
    "default_ship_to": {
        "address": "Plot 42, MIDC Chakan, Pune 410501",
        "state": "Maharashtra"
    },
    "order_frequency": "weekly",       # weekly | biweekly | monthly | sporadic
    "preferred_format": "email_text",  # email_text | pdf | excel | whatsapp
    "typical_products": ["HA-2041", "HA-3022", "EC-3001"],
    "price_agreements": {              # Negotiated prices (may differ from catalog)
        "HA-2041": 12.50,
        "HA-3022": 18.75
    }
}
```

**Customer tier distribution:**

| Tier | Count | Characteristics | Demo Role |
|------|-------|----------------|-----------|
| Enterprise | 3 | High volume, negotiated prices, credit limits >$100K | Mehta Industrial (demo supplier type 1) |
| Mid-market | 5 | Regular orders, standard pricing, credit limits $20-50K | Background orders |
| Small operator | 5 | Sporadic, WhatsApp-preferred, no formal PO process | India angle (Beat 6) |
| New customer | 2 | First order, no history, no credit established | Edge case testing |

### Entity 3: Supplier List (8 suppliers)

The supplier list drives the PO confirmation demo. Each supplier needs a behavioral profile that determines how the agent interacts with them.

**Schema:**

```python
supplier = {
    "id": "SUP-001",
    "name": "Sharma Steel Works",
    "contact_name": "Priya Sharma",
    "contact_email": "priya@sharmasteelworks.in",
    "response_profile": "delayed",     # responsive | delayed | problematic | silent
    "avg_response_hours": 72,          # Average time to confirm POs
    "confirmation_format": "email_text", # email_text | pdf | excel
    "reliability_score": 0.78,         # Historical OTIF percentage
    "products_supplied": ["SS-1001", "SS-1042", "SS-1015"],
    "sole_source_for": [],             # Products where this is the only supplier
    "price_change_history": [],        # For context retrieval
    "communication_style": "informal"  # formal | informal | terse
}
```

**Supplier behavioral profiles:**

| Profile | Count | Behavior | Agent Response |
|---------|-------|----------|----------------|
| Responsive | 3 | Confirms within 24h, clean data, no discrepancies | Auto-process, no follow-up needed |
| Delayed | 2 | Confirms in 48-96h, minor discrepancies (dates), needs follow-up | Follow-up automation, tolerance-based auto-accept |
| Problematic | 2 | Price changes, quantity modifications, format inconsistencies | Escalation to buyer, context retrieval, human approval |
| Silent | 1 | Never responds to initial PO, requires 2-3 follow-ups + phone escalation | Full follow-up ladder, eventual human takeover |

**Gupta Polymers --- the sole-source exception:** This supplier is specifically designed to trigger the escalation in Beat 4 of the demo. It must be configured as sole-source for 3-4 polymer/resin products. Its price history must show a 3% increase in January 2026 (creating retrievable context). Its confirmation for the demo must show a further 5.7% increase over the PO price, exceeding the 2% auto-accept threshold. This is a scripted fixture, not randomly generated.

### Entity 4: Sample Orders (25 orders)

Orders come in multiple formats. This is the core differentiator of the order intake agent --- it handles whatever format arrives. The 25 orders break down by format:

| Format | Count | Generation Method | Complexity |
|--------|-------|-------------------|------------|
| Email body text | 10 | Gemini-generated, 5 clean + 5 with issues | Low to medium |
| PDF attachment | 6 | Python-generated PDF (ReportLab or WeasyPrint) | Medium |
| Excel attachment | 4 | openpyxl-generated .xlsx | Medium |
| WhatsApp text | 3 | Short informal messages, Hindi-English mix | Low |
| WhatsApp voice note (transcript) | 2 | Pre-recorded audio, Gemini transcription | High |

**Order complexity distribution:**

| Complexity | Count | Characteristics |
|------------|-------|----------------|
| Clean (no issues) | 10 | All fields present, SKU matches exactly, price matches, credit OK |
| Minor issues | 8 | Alias instead of SKU, slight price discrepancy within tolerance, address variation |
| Major issues | 5 | Missing fields, price above tolerance, credit limit exceeded, unknown product, duplicate PO |
| Edge cases | 2 | Empty email body with PDF only, multi-item order with mixed clean/exception lines |

**Gemini as email generator:** Use Gemini 2.5 Flash to generate the email bodies. Prompt structure:

```
Generate a realistic purchase order email from {customer_name} ({customer_tier} tier, 
{communication_style} style) to a manufacturing company. The order is for:
- {quantity} units of "{product_alias}" (internal SKU: {sku})
- Requested delivery: {date}
- Ship to: {address}

{if has_issues}: Include these issues naturally: {issue_description}

Requirements:
- Sound like a real procurement email, not a template
- Use the product alias, NOT the internal SKU
- {tier-specific style: enterprise=formal with PO number, small=casual WhatsApp style}
- Do NOT include any information not listed above
```

This is cheaper and more realistic than hand-writing 25 emails. At Gemini Flash pricing, generating 25 emails costs less than $0.01. The key constraint in the prompt is "Do NOT include any information not listed above" --- this prevents the LLM from hallucinating order details that would break the validation pipeline.

### Entity 5: PO Confirmations (18 confirmations)

PO confirmations are the inbound data for the PO Confirmation Agent. They represent supplier responses to purchase orders.

| Type | Count | Characteristics |
|------|-------|----------------|
| Clean confirmation | 6 | All fields match PO exactly, standard format |
| Date change (within tolerance) | 3 | Delivery date shifted 1-5 days, everything else matches |
| Date change (outside tolerance) | 2 | Delivery date shifted 10+ days, triggers escalation |
| Price change (within tolerance) | 2 | Price within 2% of PO, auto-accepted |
| Price change (outside tolerance) | 2 | Price 3-10% above PO, triggers escalation |
| Quantity change | 1 | Partial fulfillment, 80% of ordered quantity |
| No response (ghost) | 2 | Supplier never confirms, triggers follow-up ladder |

**Confirmation format distribution:**

| Format | Count | Notes |
|--------|-------|-------|
| Email text reply | 8 | Direct replies to the original PO email thread |
| PDF attachment (formal) | 4 | Supplier's own confirmation form/letterhead |
| Excel attachment | 2 | Supplier uses their own spreadsheet template |
| Email with inline image | 2 | Screenshot of their ERP confirmation screen |
| Handwritten scan | 2 | Photo of a handwritten acknowledgment (for Gemini Vision) |

**Generation approach for confirmations:** The email-format confirmations use Gemini Flash with a prompt that includes the supplier's communication style profile:

```
Generate a PO confirmation reply from {supplier_contact} at {supplier_name}.
Communication style: {formal|informal|terse}
Original PO: {po_number} for {quantity} x {product} at ${price}, delivery {date}

{if discrepancy}: The supplier is confirming with this change: {change_description}
Write the change naturally — do not highlight it as an exception.

{if terse}: Reply in 1-2 sentences maximum.
{if informal}: Use casual tone, possibly incomplete sentences.
{if formal}: Use business letter format with reference numbers.
```

The critical instruction is "write the change naturally --- do not highlight it as an exception." Real suppliers do not say "ATTENTION: PRICE CHANGED." They say "Confirming PO 7823 at revised rate of $9.25/unit per our updated price list." The agent must catch the discrepancy from context, not from the supplier flagging it.

For PDF and Excel confirmations, generate with Python (ReportLab for PDF, openpyxl for Excel) using randomized layouts per supplier. No two suppliers use the same confirmation template --- this is realistic and tests the multi-format extraction capability.

For handwritten scans, photograph actual handwritten confirmations on paper, then scan or photograph them. This is the highest-difficulty format and demonstrates Gemini Vision's capability on the hardest input type.

### Entity 6: Historical Data (120 past orders)

The learning loop and context retrieval system (see [[Glacis-Agent-Reverse-Engineering-Learning-Loop]]) need historical precedents. When the agent encounters a price discrepancy from Gupta Polymers in the demo, it retrieves the context "Gupta raised prices 3% in January, market price for this grade rose 8% since Q3." That context must exist in the memory layer as historical records.

**What historical data enables:**

| Feature | Required History | Minimum Records |
|---------|-----------------|-----------------|
| Context retrieval for exceptions | Past exceptions with resolutions | 30 exception records |
| Supplier reliability scoring | Past PO confirmations with timing | 60 confirmation records |
| Customer ordering patterns | Past orders per customer | 80 order records |
| Price trend detection | Historical prices per product per supplier | 40 price data points |
| Duplicate order detection | Recent orders from same customers | 15 recent orders |

**Generation approach:** Use Python Faker with custom providers for the structural data (dates, amounts, SKUs), then layer Gemini-generated text on top for the natural-language fields (email bodies, confirmation notes, exception descriptions). The generation script should produce internally consistent data --- an order from Mehta Industrial always references products in Mehta's `typical_products` list, at prices in Mehta's `price_agreements`, shipped to Mehta's `default_ship_to`.

```python
from faker import Faker
from faker.providers import BaseProvider
import random
from datetime import datetime, timedelta

fake = Faker('en_IN')  # Indian locale for names, addresses, phone numbers

class SupplyChainProvider(BaseProvider):
    def po_number(self):
        return f"PO-{random.randint(1000, 9999)}"
    
    def order_status(self):
        # Weighted: most orders are completed, some are in-process
        return random.choices(
            ["completed", "confirmed", "in_transit", "pending", "exception"],
            weights=[50, 20, 15, 10, 5]
        )[0]
    
    def resolution_type(self):
        return random.choices(
            ["auto_accepted", "buyer_approved", "supplier_corrected", 
             "order_amended", "order_cancelled"],
            weights=[60, 20, 10, 7, 3]
        )[0]

fake.add_provider(SupplyChainProvider)
```

**Faker locale:** Use `en_IN` (Indian English) for generating names, addresses, and phone numbers. This produces contextually appropriate data for the India track --- "Sharma," "Mehta," "Gupta" as supplier names, Maharashtra/Gujarat/Tamil Nadu addresses, +91 phone numbers. Faker's Indian locale is well-maintained and produces realistic output without manual curation.

### Firestore Seeding Strategy

All data loads into Firestore as the system's operational database. The seeding script runs once before the demo and populates six collections:

```
firestore/
  products/          # 80 documents (product catalog)
  customers/         # 15 documents (customer profiles + credit)
  suppliers/         # 8 documents (supplier profiles + behavior)
  orders/            # 25 active + 120 historical = 145 documents
  po_confirmations/  # 18 active + 60 historical = 78 documents
  exceptions/        # 30 historical exception records with resolutions
```

**Seeding order matters.** Products first (no dependencies). Then suppliers and customers (reference products). Then historical orders (reference customers and products). Then historical confirmations (reference orders and suppliers). Then active demo data last. This prevents orphaned references.

The seeding script should be idempotent --- running it twice should produce the same result, not duplicate data. Use deterministic document IDs based on entity type and sequence number (`product/HA-2041`, `customer/CUST-001`, `order/ORD-2024-0001`).

## Data Sources and Inspiration

### Kaggle Datasets (Reference, Not Direct Use)

| Dataset | Use | Link |
|---------|-----|------|
| DataCo Smart Supply Chain | Schema inspiration for order fields, delivery status categories, product categories | [Kaggle](https://www.kaggle.com/datasets/shashwatwork/dataco-smart-supply-chain-for-big-data-analysis) |
| Supply Chain Dataset (Amirmotefaker) | Pricing distributions, supplier performance distributions | [Kaggle](https://www.kaggle.com/datasets/amirmotefaker/supply-chain-dataset) |
| Smart Logistics Supply Chain | Delivery timing distributions, geographic patterns | [Kaggle](https://www.kaggle.com/datasets/ziya07/smart-logistics-supply-chain-dataset) |

These datasets are not used directly. They inform realistic distributions --- what percentage of orders are late, what price variance ranges look like, how delivery times distribute across carriers. The actual demo data must be custom-generated to match the specific supplier personas and demo beats.

### Google's Synthetic Data Generation Pattern

Google Cloud Platform publishes a [Gemini synthetic data generation notebook](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/data-generation/synthetic_data_generation_using_gemini.ipynb) demonstrating the Snowfakery + Gemini pattern. The approach: define a structural schema with Faker/Snowfakery for consistent relational data, then use Gemini to generate natural-language fields (email text, confirmation notes, exception descriptions) that reference the structured data. This hybrid approach produces data that is structurally sound (no orphaned references, consistent pricing, valid dates) and linguistically realistic (varied email styles, natural phrasing, realistic supplier communication patterns).

### Academic Context

A 2024 survey in the International Journal of Production Research ("Leveraging synthetic data to tackle machine learning challenges in supply chains") catalogs the state of the art. The key finding: hybrid methods combining structural generators (Faker, Bayesian networks) with generative models (LLMs, GANs) outperform either approach alone. For tabular data, constraint-based generation preserving "statistical moments of real data" matters more than model sophistication. For text data, LLMs dominate. The recommendation maps directly to the Faker-for-structure, Gemini-for-text approach.

## Generation Pipeline

The complete generation pipeline runs in five stages, each building on the previous:

### Stage 1: Master Data (products, customers, suppliers)

**Tool:** Python script with hand-curated data + Faker for padding.

The 3 demo suppliers (Mehta, Sharma, Gupta) and their associated products are hand-written --- no generation. These are scripted fixtures with exact values needed for the demo beats. The remaining 5 suppliers, 12 customers, and ~65 products are Faker-generated with the Indian locale and the `SupplyChainProvider`.

**Output:** 3 JSON files (`products.json`, `customers.json`, `suppliers.json`).

**Validation:** Every product must have at least one supplier. Every supplier must supply at least two products. Every customer must have at least three products in their `typical_products` list. These constraints prevent impossible orders during later generation stages.

### Stage 2: Historical Data (orders, confirmations, exceptions)

**Tool:** Python script using master data as input, Faker for dates/amounts, weighted random for status distributions.

Generate 120 historical orders spanning the past 6 months. Distribution: 60% completed normally, 20% had minor exceptions (auto-resolved), 15% had major exceptions (buyer-resolved), 5% cancelled. Each order references real products and customers from Stage 1.

Generate 60 historical PO confirmations with timing distributions matching the supplier behavioral profiles. Responsive suppliers confirm in 12-24h. Delayed suppliers confirm in 48-96h. Problematic suppliers confirm in 72-120h with discrepancies in 40% of cases.

Generate 30 historical exception records with resolutions. These populate the memory layer for context retrieval. At least 3 must be price exceptions from Gupta Polymers (to provide the "raised prices 3% in January" context).

**Output:** 3 JSON files (`historical_orders.json`, `historical_confirmations.json`, `historical_exceptions.json`).

### Stage 3: Demo Fixtures (the scripted scenarios)

**Tool:** Hand-written JSON + Gemini-generated email text.

Create the exact 3 demo scenarios from [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]:

1. **Mehta Industrial order email** --- clean order, all fields present, SKU alias that matches via embedding search, price matching contract, sufficient credit (barely).
2. **Sharma Steel PO + follow-up thread + confirmation** --- original PO, 48h timeout, follow-up email, supplier reply with 3-day date slip.
3. **Gupta Polymers PO + confirmation with price change** --- original PO at $8.75, confirmation at $9.25, sole-source flag, historical context.

Each fixture includes the raw email text (Gemini-generated), the expected extraction output (hand-verified), and the expected system behavior (auto-accept, follow-up, escalate).

**Output:** `demo_fixtures/` directory with subdirectories per scenario.

### Stage 4: Email and Document Generation

**Tool:** Gemini Flash for email text, ReportLab for PDFs, openpyxl for Excel files.

Generate the 25 active orders across all formats. For each order, the script:
1. Selects a customer and products from master data
2. Determines order complexity (clean, minor issue, major issue) from the distribution table
3. Generates the email body or document content via Gemini Flash
4. If PDF: renders via ReportLab with a randomized template (different customers use different PO forms)
5. If Excel: generates via openpyxl with a randomized column layout
6. Saves both the raw document and the expected extraction JSON for testing

Generate the 18 PO confirmations similarly, using supplier profiles to determine format and communication style.

**Output:** `emails/` and `documents/` directories with raw files + expected outputs.

### Stage 5: Firestore Seeding

**Tool:** Python script using `firebase-admin` SDK.

Load all data from Stages 1-4 into Firestore in dependency order. The script:
1. Deletes existing demo data (idempotent reset)
2. Loads products, customers, suppliers (no dependencies)
3. Loads historical data (references entities)
4. Loads active demo data (references entities)
5. Generates embeddings for the product catalog aliases (for SKU matching)
6. Generates embeddings for historical exception descriptions (for context retrieval)
7. Validates: no orphaned references, all demo fixtures loadable, all supplier profiles complete

**Output:** Populated Firestore database ready for demo.

**Total generation time estimate:** 15-20 minutes for the Python scripts, plus 2-3 minutes for Gemini API calls (25 emails + 18 confirmations at ~2s each). Firestore seeding: under 30 seconds for ~300 documents.

## Tradeoff Analysis

### Hand-Written vs Generated Demo Fixtures

**Hand-written:** Exact control over every word. Guarantees the demo beats work exactly as scripted. Time-consuming for 25+ documents.

**Generated with Gemini:** Fast, realistic variety. But the LLM might hallucinate details that break the validation pipeline (wrong prices, impossible dates, products not in the catalog).

**Decision:** Hybrid. The 3 primary demo fixtures (Mehta order, Sharma confirmation, Gupta confirmation) are hand-written email text with Gemini polishing for naturalness. The remaining 22 orders and 15 confirmations are Gemini-generated from structured prompts with post-generation validation against the product catalog and pricing data. Any generated email that references a product not in the catalog gets regenerated.

### Indian Context vs Generic

**Indian names/addresses/products:** Contextually appropriate for the India track. Judges expect it. Faker's `en_IN` locale handles the structural data.

**Generic (US/EU):** Easier to verify, more familiar to international judges.

**Decision:** Indian. The Google Solution Challenge India track evaluates alignment with the Indian context. Fictional Indian company names (Mehta, Sharma, Gupta), Indian addresses (Pune, Ahmedabad, Chennai), and Indian product contexts (auto parts, textiles, industrial components) all reinforce the submission's geographic specificity. The enterprise metrics in the dashboard (Pfizer, Carlsberg) are global --- the demo scenario itself is Indian.

### Minimal Viable Data vs Rich Dataset

**Minimal (3 suppliers, 10 products, 10 orders):** Fast to generate. But the demo looks like a toy --- "oh, it works on 10 pre-set orders."

**Rich (8 suppliers, 80 products, 145 orders):** The dashboard in Beat 5 shows a scrolling activity feed of 24 hours of agent activity. That feed needs enough background data to look like a real production system. 145 orders across 15 customers and 8 suppliers produces enough variety for the feed to scroll convincingly for 15 seconds.

**Decision:** Rich. The generation is automated, so 80 products costs the same engineering time as 10 products. The Faker + Gemini pipeline scales linearly. The only cost is Gemini API calls, which at Flash pricing for 43 emails/confirmations totals under $0.05.

### Embedding Pre-computation vs Runtime Generation

**Pre-computed:** Generate all product alias embeddings and historical exception embeddings at seed time. Store in Firestore. Faster retrieval during demo (no embedding API calls at runtime).

**Runtime:** Generate embeddings on demand. Simpler seeding script. But adds 200-500ms latency per embedding call during the demo, and Gemini's embedding API has rate limits.

**Decision:** Pre-computed. The demo cannot afford any latency that is not visually interesting. A 500ms delay for an embedding call looks like the system is stalling. Pre-computing 400 embeddings (80 products x 5 aliases each) takes about 30 seconds at Gemini's embedding rate and costs under $0.01.

## Five Misconceptions About Demo Data

1. **"Random data is realistic enough."** It is not. Random data produces uniform distributions. Real supply chain data follows power laws --- 20% of customers generate 80% of orders, 3 suppliers handle 60% of spend, most orders are routine but the exceptions cluster around specific supplier-product pairs. The generation script must encode these distributions explicitly via weighted random selection, not uniform sampling.

2. **"More data means a better demo."** Past a threshold, additional data is invisible to the audience. The judges will see the 3 demo scenarios in detail and the dashboard feed for 15 seconds. Whether the database has 145 or 1,450 orders behind the dashboard makes zero difference to the score. The threshold for "looks real" is approximately 100+ historical records. Beyond that, invest the time in polishing the 3 demo fixtures instead.

3. **"The email text does not matter --- the system parses structured data anyway."** The entire premise of the order intake agent is that it handles unstructured input. If every demo email says "Please order 200 units of SKU HA-2041 at $12.50 each," you have not demonstrated unstructured parsing. The emails must include natural language ("the usual hydraulic parts, same as last month but double the quantity"), aliases, missing fields, and conversational tone. This is where Gemini generation is non-negotiable.

4. **"Generate everything at once."** Sequential generation with validation between stages catches problems that batch generation misses. If Stage 2 generates an order referencing a product deleted during Stage 1 revisions, the seeding script breaks at Stage 5. The five-stage pipeline with validation at each stage boundary prevents cascading data inconsistencies.

5. **"Synthetic data is a one-time task."** It is not. Every time you modify the product catalog (add a product, change a price, rename a category), every downstream fixture that references that product must be regenerated or updated. The generation scripts must be parameterized and re-runnable, not one-off notebooks. The Firestore seeding script must be idempotent. This is infrastructure, not a chore.

## What This Note Does Not Cover

- The demo scenario that consumes this data --- that is [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]]
- The Firestore schema design for storing this data --- that is [[Glacis-Agent-Reverse-Engineering-Firestore-Schema]]
- The embedding-based item matching that uses product aliases --- that is [[Glacis-Agent-Reverse-Engineering-Item-Matching]]
- The build sequence for implementing the generation pipeline --- that is [[Glacis-Agent-Reverse-Engineering-Build-Plan]]
- The prompt templates for Gemini extraction --- that is [[Glacis-Agent-Reverse-Engineering-Prompt-Templates]]

## Sources

- [DataCo Smart Supply Chain for Big Data Analysis](https://www.kaggle.com/datasets/shashwatwork/dataco-smart-supply-chain-for-big-data-analysis) --- Kaggle dataset, schema inspiration for order and delivery fields
- [Supply Chain Dataset (Amirmotefaker)](https://www.kaggle.com/datasets/amirmotefaker/supply-chain-dataset) --- pricing and supplier performance distributions
- [Leveraging synthetic data to tackle ML challenges in supply chains](https://www.tandfonline.com/doi/full/10.1080/00207543.2024.2447927) --- IJPR 2024 survey, hybrid generation methods, quality evaluation
- [Synthetic Data Generation using Gemini APIs](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/data-generation/synthetic_data_generation_using_gemini.ipynb) --- Google Cloud Platform, Snowfakery + Gemini pattern
- [Creating Synthetic Data with Python Faker](https://www.datacamp.com/tutorial/creating-synthetic-data-with-python-faker-tutorial) --- Faker library tutorial, custom providers, locale support
- [Generate Synthetic Business Data with Gemini](https://arjunraghunandanan.medium.com/how-to-generate-synthetic-dummy-business-data-with-gemini-07e2179b448e) --- Gemini-based generation with domain schemas
- [[Supply-Chain-Seed-Data-Generation]] --- Companion note on seed data for the exception triage demo, sizing principles
- [[Glacis-Agent-Reverse-Engineering-Demo-Scenario]] --- The demo narrative this data supports
- [[Glacis-Agent-Reverse-Engineering-Overview]] --- Enterprise metrics from Glacis whitepapers (Pfizer, Knorr-Bremse, IDEX)
