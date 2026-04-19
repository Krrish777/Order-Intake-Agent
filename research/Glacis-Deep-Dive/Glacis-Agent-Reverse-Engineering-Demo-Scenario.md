---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Demo Scenario Design"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 5
date: 2026-04-08
tags:
  - research
  - supply-chain
  - demo
  - hackathon
  - google-solution-challenge
  - presentation
---

# Demo Scenario Design

> [!info] Context
> Depth level: 5. Parent: [[Glacis-Agent-Reverse-Engineering-Overview]]. Siblings: [[Glacis-Agent-Reverse-Engineering-Synthetic-Data]], [[Glacis-Agent-Reverse-Engineering-Build-Plan]]

## The Problem

You have 120 seconds. The Google Solution Challenge judges will watch hundreds of demo videos. Most of them will be forgettable --- another dashboard, another chatbot, another "we used Gemini to summarize things." The judges score on Technical Merit (40%), Innovation and Creativity (25%), Alignment with Theme (25%), and User Experience (10%). Your demo must score high on all four in two minutes flat.

The constraint is brutal. Two minutes forces you to cut everything that does not directly prove your thesis. No "let me walk you through the architecture." No "here's how we set up the database." The judges do not care about your Firestore schema. They care about one thing: does this solution do something they have not seen before, and does it work?

The specific challenge for an order intake and PO confirmation agent is that the value is invisible. When a human buyer processes an order in 8 minutes, you see the effort. When an AI agent processes it in 45 seconds, all you see is... a record appearing in a table. The processing itself --- the extraction, validation, enrichment, confidence scoring, routing --- happens inside the system. Making that process visible, dramatic, and comprehensible in 120 seconds is the entire design challenge.

Hackathon demo veterans converge on one structural principle: the before/after comparison. Devpost's official guidance says to "highlight the problem, solution, and functionality" and to "demonstrate the project running with sample inputs to show judges tangible results." The Google Solution Challenge specifically requires "an actual working application (not a mockup)" with evidence of "how a user will interact with the solution." You cannot fake this with slides. You need a live system processing real (synthetic) data.

## First Principles

A demo is a narrative, not a feature tour. The narrative has exactly one arc: chaos to control. Every second must advance that arc or it gets cut.

The structure borrows from screenwriting. A two-minute film has the same beats as a two-hour film --- they are just compressed. The hook establishes stakes in 10-15 seconds. The rising action shows the system confronting progressively harder challenges. The climax is the moment the system does something a human would find genuinely impressive. The resolution shows the aggregate impact.

Three principles govern the design:

**Show, do not tell.** Every claim about the system must be demonstrated live. "Our agent processes orders in under 60 seconds" is a slide. Watching an email arrive and an order appear in the ERP within 45 seconds is a demo. The former is forgettable. The latter is memorable.

**Escalate difficulty.** Start with a clean, easy case. Then hit the system with a harder one. Then hit it with something that would stump most automation. The progression from "works perfectly" to "handles the unexpected" to "catches what a human would miss" builds credibility in a way that a single showcase cannot.

**Feature the human-in-the-loop.** Judges are skeptical of fully autonomous AI. Rightly so. The moment where the agent says "I am not confident enough to handle this myself, here is what I found, what do you want to do?" and the human clicks "Approve" --- that is the moment judges trust the system. It proves graduated autonomy, not reckless automation.

## The Demo Narrative: 120 Seconds

### Beat 1: The Hook (0:00--0:15)

**What the judge sees:** A split screen. Left side: a procurement buyer's inbox with 47 unread emails from suppliers. Right side: a spreadsheet with 23 rows of purchase orders, each highlighted in red/yellow/green showing confirmation status. A timer overlay reads "Average time to process: 8-15 minutes per order."

**What the narrator says:** "Every day, procurement teams spend 60-70% of their time on this --- reading supplier emails, cross-checking PO confirmations against ERP data, chasing suppliers who have not responded, and flagging price discrepancies. At a $1.5B manufacturer, this costs 30 full-time employees and $3.1 million per year."

**Why this works:** The hook does two things simultaneously. It establishes the problem with a concrete visual (the inbox, the spreadsheet) and quantifies the cost with Glacis's published numbers. Judges who have never worked in procurement understand the problem in five seconds: email overload, manual cross-checking, money wasted.

**Technical notes for recording:** The inbox and spreadsheet are pre-populated with synthetic data (see [[Glacis-Agent-Reverse-Engineering-Synthetic-Data]]). The timer overlay can be added in post-production. The left/right split is achievable with OBS Studio scene composition.

### Beat 2: Order Intake --- The Clean Path (0:15--0:45)

**What the judge sees:** A customer email arrives in real time. The narrator clicks on it. It is a free-text email from "Rajesh Mehta, Mehta Industrial Supplies" ordering "200 units of Grade-A hydraulic actuators, Part #HA-2041, $12.50/unit, delivery by April 22, ship to our Pune warehouse."

The screen transitions to the agent dashboard. A processing timeline appears:
1. "Email received" (0s)
2. "Extracting order data..." (2s) --- a structured JSON appears with product, quantity, price, delivery date, ship-to
3. "Validating against product master..." (4s) --- SKU match confirmed, green checkmark
4. "Price check: $12.50 vs contract $12.50" (5s) --- match confirmed, green checkmark
5. "Inventory check: 200 available at Pune DC" (6s) --- available, green checkmark
6. "Credit check: Mehta Industrial, $47,100 outstanding of $50,000 limit" (7s) --- passed, green checkmark
7. "Sales order SO-2024-0847 created in ERP" (8s) --- confirmation email auto-sent to customer

Total elapsed: under 10 seconds on screen. The narrator overlay reads "45 seconds end-to-end vs 8-15 minutes manually."

**What the narrator says:** "A customer emails an order. Our AI agent --- built on Google ADK with Gemini 2.5 --- extracts the order data, validates against the product catalog and price book, checks inventory and credit, and creates the sales order. Forty-five seconds, zero human intervention. The customer gets a confirmation email immediately."

**Why this works:** This is the "happy path" --- the baseline that proves the system functions. Judges need to see it work perfectly once before they believe it can handle exceptions. The step-by-step processing timeline makes the invisible visible. Each green checkmark is a validation the human buyer would have done manually. The 45-second timestamp makes the before/after comparison concrete.

**Supplier type demonstrated:** Supplier Type 1 --- Responsive. Clean data, standard format, all values within tolerance.

### Beat 3: PO Confirmation --- The Delayed Supplier (0:45--1:15)

**What the judge sees:** The dashboard shifts to the PO Confirmation view. A list of outstanding purchase orders appears. One row is highlighted amber: "PO-7823, Sharma Steel Works, sent 3 days ago, no response."

The agent's activity log shows:
1. "PO-7823 sent to Sharma Steel Works --- April 5"
2. "48-hour SLA breached --- Follow-up #1 sent automatically --- April 7"
3. "Follow-up #1: 'Hi Priya, just checking in on PO #7823 for 500 steel billets. Could you confirm the April 22 delivery date when you get a chance?'"

Then, live: an email arrives from Priya at Sharma Steel. The agent processes it in real time.

4. "Response received from Sharma Steel --- April 8"
5. "Extracting confirmation..." --- a structured comparison table appears:

| Field | PO Value | Supplier Confirmed | Match? |
|-------|----------|-------------------|--------|
| Quantity | 500 | 500 | Yes |
| Unit Price | $34.00 | $34.00 | Yes |
| Delivery Date | April 22 | April 25 | **No --- 3 days late** |

6. "Discrepancy detected: delivery date slip of 3 days"
7. "Within tolerance (5-day buffer configured) --- auto-accepted, ERP updated"
8. "Confirmation email sent to Sharma Steel acknowledging April 25"

**What the narrator says:** "Now the procurement side. Purchase order 7823 went out three days ago --- no response from the supplier. The agent automatically followed up with a natural-language email --- not a robot reminder, a message indistinguishable from what a real buyer would write. When the supplier finally responds, the agent extracts the confirmation, cross-references every field against the original PO, detects a 3-day delivery date slip, checks it against our configured tolerance, auto-accepts it, and updates the ERP. The buyer never had to look at this."

**Why this works:** This beat demonstrates three capabilities in 30 seconds: automated follow-up (the supplier communication engine), real-time extraction and comparison (the validation pipeline), and autonomous decision-making within tolerance (the exception handling system). The natural-language follow-up email is a differentiator --- judges can read it and see it sounds human. The comparison table makes the validation visible.

**Supplier type demonstrated:** Supplier Type 2 --- Delayed. Requires follow-up, responds with a minor discrepancy within tolerance.

### Beat 4: The Exception --- Price Mismatch Escalation (1:15--1:40)

**What the judge sees:** Another supplier email arrives --- this one from "Gupta Polymers." The agent processes it. The comparison table appears, but this time a row flashes red:

| Field | PO Value | Supplier Confirmed | Match? |
|-------|----------|-------------------|--------|
| Quantity | 1,000 | 1,000 | Yes |
| Unit Price | $8.75 | $9.25 | **No --- 5.7% above PO** |
| Delivery Date | April 20 | April 20 | Yes |

The agent's decision log reads:
- "Price discrepancy: 5.7% above PO value"
- "Exceeds auto-accept threshold (2%)"
- "Financial impact: $500 additional cost on this PO"
- "Recommendation: ESCALATE to buyer --- Gupta Polymers is sole-source for this resin"
- "Context retrieved: Gupta raised prices 3% in January, market price for this grade rose 8% since Q3"

The dashboard shows a one-click approval panel: "Accept revised price ($9.25/unit)?" with "Approve," "Reject," or "Counter-offer" buttons. The narrator clicks "Approve." The ERP updates. A confirmation email goes to Gupta Polymers.

**What the narrator says:** "Here is where it gets interesting. Gupta Polymers confirmed the PO but raised the price 5.7%. That exceeds our auto-accept threshold. The agent does not just flag the discrepancy --- it calculates the financial impact, notes that Gupta is our sole source for this material, and pulls context from previous interactions showing a market-wide price increase. The buyer sees all of this and makes the call with one click. The agent handles everything else."

**Why this works:** This is the climax of the demo. It demonstrates the graduated autonomy model --- the agent handles routine decisions automatically (Beat 3) but escalates genuinely ambiguous decisions to humans with full context (Beat 4). The "sole-source supplier" detail and the market price context show that the system has memory and reasoning, not just pattern matching. The one-click approval shows UX that respects human judgment without wasting human time.

**Supplier type demonstrated:** Supplier Type 3 --- Problematic. Responds with a price change exceeding tolerance, requiring human escalation.

### Beat 5: The Dashboard --- Before/After Metrics (1:40--1:55)

**What the judge sees:** The screen transitions to a metrics dashboard showing two columns:

| Metric | Before (Manual) | After (AI Agent) |
|--------|-----------------|-------------------|
| Order processing time | 8-15 min | <60 sec |
| PO confirmation rate (48hr) | 34% | 92% |
| Buyer time on confirmations | 60-70% of day | 10-15% of day |
| Price discrepancies caught | ~70% | 99%+ |
| Touchless order rate | 0% | 80-92% |
| Annual cost (30 FTE equivalent) | $1.9M | ~$380K |

Below the table, a real-time activity feed scrolls showing the last 24 hours of agent activity: orders processed, POs confirmed, follow-ups sent, exceptions escalated.

**What the narrator says:** "The results. Processing time from 8-15 minutes to under 60 seconds. PO confirmation rates from 34% to 92% within 48 hours. Touchless order rates of 80 to 92 percent. These are not projections --- they are enterprise metrics from Pfizer, Carlsberg, Knorr-Bremse, and IDEX Corporation, achieved with this exact architecture."

**Why this works:** Judges need quantification. Abstract claims about "AI-powered efficiency" mean nothing. Specific numbers from named companies --- which are all published in Glacis's whitepapers and publicly verifiable --- carry weight. The dashboard also demonstrates real-time monitoring capability, hitting the Technical Merit criteria.

### Beat 6: The Close --- India Angle (1:55--2:00)

**What the judge sees:** A WhatsApp interface appears. A message comes in from a small operator: a voice note. The agent transcribes it via Gemini, extracts the order data, and displays the same processing timeline as Beat 2.

**What the narrator says:** "And the same platform works over WhatsApp --- voice notes, images, text --- for India's 14 million small logistics operators who coordinate entirely through messaging. No portal. No training. They keep working the way they already work."

**Why this works:** Five seconds. That is all the India angle needs. It demonstrates two things: channel extensibility (the system is not email-only) and SDG alignment (accessible to underserved operators). It hits the Alignment with Theme criterion hard in minimal time. The WhatsApp Cloud API integration is also a Google technology differentiator most submissions will not have.

## Supplier Type Design

The demo requires three distinct supplier personas that collectively exercise every system capability:

### Type 1: The Responsive Supplier (Mehta Industrial Supplies)

- **Behavior:** Sends clean, well-formatted orders. Confirms POs within 24 hours. No discrepancies.
- **Demo role:** Proves the happy path works. Establishes baseline processing speed.
- **Data requirements:** Standard product catalog items, prices matching the contract, sufficient inventory.
- **Communication style:** Professional, structured emails. Uses PO numbers correctly. Includes all required fields.

### Type 2: The Delayed Supplier (Sharma Steel Works)

- **Behavior:** Does not respond within SLA. Confirms after automated follow-up. Minor delivery date discrepancy within tolerance.
- **Demo role:** Demonstrates follow-up automation and within-tolerance auto-acceptance. Shows the communication engine producing human-quality emails.
- **Data requirements:** Products with delivery buffers configured. Tolerance thresholds set to accept 3-5 day delivery slips.
- **Communication style:** Brief, informal replies. "Hi, confirming PO 7823. We can do the 500 billets but need until April 25. Thanks, Priya."

### Type 3: The Problematic Supplier (Gupta Polymers)

- **Behavior:** Confirms with a price change exceeding tolerance. Sole-source supplier for a critical material.
- **Demo role:** Demonstrates the full escalation path. Shows context retrieval, financial impact calculation, and human-in-the-loop approval.
- **Data requirements:** Price configured below the supplier's stated price. Sole-source flag in supplier master. Historical price changes in the memory layer.
- **Communication style:** Formal but unapologetic. "Please note revised pricing of $9.25/unit reflecting raw material cost increases effective April 1."

### Why Three Is the Right Number

Two suppliers demonstrate automation. Three suppliers demonstrate intelligence. The progression from clean to delayed to problematic creates a narrative arc within the demo itself. Each supplier type exercises a different system capability:

| Capability | Type 1 | Type 2 | Type 3 |
|-----------|--------|--------|--------|
| Extraction | Yes | Yes | Yes |
| Validation | Yes | Yes | Yes |
| Follow-up automation | No | Yes | No |
| Tolerance-based auto-accept | No | Yes | No |
| Escalation to human | No | No | Yes |
| Context retrieval | No | No | Yes |
| One-click approval | No | No | Yes |
| WhatsApp channel | --- | --- | --- |

The WhatsApp demo in Beat 6 implicitly introduces a fourth persona --- the small Indian operator --- but does not require a full supplier profile. A single voice note suffices.

## Scoring Strategy

Mapping every demo beat to the judging criteria:

### Technical Merit (40%)

- **Multi-service Google integration:** Gmail API (email ingestion), Gemini 2.5 (extraction + reasoning), ADK (agent orchestration), Pub/Sub (event routing), Firestore (state + memory), Cloud Run (deployment), WhatsApp Cloud API (channel extensibility)
- **Demonstrated in:** Beats 2-6 show all services working together in a live pipeline
- **Depth signal:** The step-by-step processing timeline in Beat 2 shows judges that the system is not a single API call but a multi-step pipeline with validation at each stage

### Innovation and Creativity (25%)

- **Anti-Portal principle:** The system adapts to humans, not the other way around. No supplier portal, no format requirements, no training needed.
- **Graduated autonomy:** Three levels of decision-making demonstrated across Beats 2-4
- **Natural-language communication:** The follow-up email in Beat 3 sounds human. Most competing submissions will use template-based notifications.
- **Demonstrated in:** Beat 3 (communication quality) and Beat 4 (contextual escalation with memory)

### Alignment with Theme (25%)

- **"Intelligence layers":** The agent IS the intelligence layer wrapping around existing email communication
- **"Monitor transit signals":** PO confirmation monitoring is signal monitoring applied to procurement
- **"Predict disruption risks early":** Price discrepancy detection and sole-source context IS early risk prediction
- **India angle:** WhatsApp accessibility for small operators. Beat 6 makes this explicit.
- **Demonstrated in:** Every beat maps to theme language. Beat 6 specifically addresses the India SDG alignment.

### User Experience (10%)

- **One-click approval:** Beat 4's approval panel is the UX moment
- **Processing timeline:** Beat 2's step-by-step visualization makes complex AI processing comprehensible
- **Dashboard:** Beat 5's metrics view shows operational UX
- **Demonstrated in:** Beats 2, 4, and 5

## Recording Strategy

**Tool:** OBS Studio for screen capture with scene composition. Audacity for narration. DaVinci Resolve (free) for editing.

**Approach:** Pre-record each beat separately, then cut together. This allows retakes on individual sections without re-running the entire demo. The system must be running live for each beat --- no mockups --- but you can choose the best take from multiple attempts.

**Narration:** Write the exact script (provided above in each beat). Read it at a measured pace --- approximately 150 words per minute. The total narration across all beats is approximately 350 words, which fits comfortably in 120 seconds with pauses for visual processing.

**Resolution:** 1080p minimum. Judges watch on laptop screens, not phones. Text must be readable at 100% zoom.

**Captions:** Add subtitles. Some judges watch with sound off during initial screening.

## Five Mistakes That Kill Demo Videos

1. **Starting with architecture slides.** Judges click away in the first 10 seconds if they see a system diagram. Start with the problem, not the solution. Show the messy inbox, not the clean architecture.

2. **Showing only the happy path.** If every order processes perfectly, judges assume you have not handled edge cases. The exception in Beat 4 is not optional --- it is the moment that separates a prototype from a product.

3. **Speeding up the narration to fit more features.** Two minutes at normal pace covers 300-350 words. That is enough for six beats. If you are speeding up, you are cramming, and cramming signals poor prioritization. Cut features rather than speed up delivery.

4. **Demoing a mockup.** The Google Solution Challenge explicitly requires "an actual working application (not a mockup)." Judges will notice if the data does not change between screens. The system must process the synthetic emails live, even if the emails are pre-seeded.

5. **Ignoring the India angle.** Theme 3 submissions without an India-specific accessibility story are leaving 25% of the Alignment score on the table. Five seconds of WhatsApp integration is worth more than 30 seconds of additional feature demonstration.

## Tradeoff Analysis

### Scripted Demo vs Live Demo

**Scripted (pre-recorded with best takes):** Reliable, polished, no risk of live failure. Allows scene composition and overlay effects. You control timing exactly.

**Live (single uncut recording):** More authentic. Judges trust it more. But any failure --- a network timeout, a Gemini rate limit, a Firestore cold start --- destroys the submission.

**Decision:** Pre-record individual beats with the live system, then edit together. This gives the authenticity of live processing with the reliability of post-production. The Google Solution Challenge does not require a single uncut take.

### Depth vs Breadth in Feature Coverage

**Depth (fewer features, more detail):** Shows engineering quality. Risks looking like a narrow tool.

**Breadth (more features, less detail):** Shows platform potential. Risks looking superficial.

**Decision:** Depth. The six-beat structure covers three major capabilities (order intake, PO confirmation with follow-up, exception escalation) plus the WhatsApp extension. That is enough breadth. Within each beat, the step-by-step processing timeline provides depth. Adding a fourth capability (e.g., the learning loop, the SOP playbook) would require cutting the exception beat, which is the demo's strongest moment.

### Real Company Names vs Fictional

**Real names (Pfizer, Knorr-Bremse):** Impressive, but raises questions about whether you actually work with them. In a hackathon context, it sounds like you are claiming partnerships you do not have.

**Fictional Indian names (Mehta Industrial, Sharma Steel, Gupta Polymers):** Contextually appropriate for the India track. Avoids any implication of real partnerships. Makes the demo feel like a realistic Indian procurement scenario rather than a Silicon Valley pitch.

**Decision:** Fictional Indian company names in the demo. Reference real enterprise metrics (Pfizer, Carlsberg, Knorr-Bremse) only in the metrics dashboard and narration, attributed to their published sources.

## What This Note Does Not Cover

- The synthetic data needed to run this demo --- that is [[Glacis-Agent-Reverse-Engineering-Synthetic-Data]]
- The build sequence to implement the demo --- that is [[Glacis-Agent-Reverse-Engineering-Build-Plan]]
- The deployment architecture for the live system --- that is [[Glacis-Agent-Reverse-Engineering-Deployment]]
- The prompt templates driving extraction and validation --- that is [[Glacis-Agent-Reverse-Engineering-Prompt-Templates]]
- The dashboard UI implementation --- that is [[Glacis-Agent-Reverse-Engineering-Dashboard-UI]]

## Sources

- [Devpost: 6 Tips for Making a Winning Hackathon Demo Video](https://info.devpost.com/blog/6-tips-for-making-a-hackathon-demo-video) --- script preparation, timing, professional quality
- [Google Vertex AI Hackathon: Expert Tips for Submission Videos](https://googlevertexai.devpost.com/updates/30990-expert-tips-for-creating-your-hackathon-submission-video) --- elevator pitch structure, live demonstration emphasis
- [Google Solution Challenge 2026 --- Hack2Skill](https://vision.hack2skill.com/event/solution-challenge-2026) --- Technical Merit 40%, Innovation 25%, Alignment 25%, UX 10%
- [Google Developers: GDSC Solution Challenge](https://developers.google.com/community/gdsc-solution-challenge) --- "actual working application (not a mockup)" requirement
- [TAIKAI: How to Create a Winning Hackathon Pitch](https://taikai.network/en/blog/how-to-create-a-hackathon-pitch) --- narrative structure, before/after comparison pattern
- Glacis: "How AI Automates Order Intake in Supply Chain" (Dec 2025) --- Pfizer 80% touchless, $10B manufacturer case study
- Glacis: "AI For PO Confirmation V8" (March 2026) --- Knorr-Bremse >99% accuracy, IDEX 92% confirmation in 48hrs
- [[Supply-Chain-Hackathon-Submission]] --- Phase 1 deliverable requirements and scoring strategy
