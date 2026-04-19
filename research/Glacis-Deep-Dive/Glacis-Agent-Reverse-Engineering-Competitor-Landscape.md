---
type: research-deep-dive
topic: "Glacis AI Agent Reverse-Engineering: Order Intake + PO Confirmation"
subtopic: "Competitor Landscape"
overview: "[[Glacis-Agent-Reverse-Engineering-Overview]]"
depth_level: 1
date: 2026-04-08
tags:
  - research
  - supply-chain
  - competitor-analysis
  - glacis
  - pallet
  - tradeshift
  - coupa
  - basware
---

# Competitor Landscape: Stealing the Best Engineering Patterns

> [!info] Context
> This is not competitive analysis. We are not evaluating market positioning or TAM. We are reverse-engineering the **engineering patterns** that make each company's AI supply chain system work, then stealing the best ideas for our Google Solution Challenge build. Six companies, six different architectural bets. Each one got something right that the others missed.

## The Problem

You are building an AI agent that processes purchase orders from email. You have read the Glacis whitepapers. You understand their agent architecture. But Glacis is one company with one set of design tradeoffs. If you only study Glacis, you inherit their blind spots.

The real question is architectural: **What are the fundamental engineering decisions you face when building a supply chain AI agent, and which company solved each decision best?**

Every system in this space must solve the same six problems:

1. **Document ingestion** — How do you turn a PDF/email/spreadsheet into structured data?
2. **Validation & matching** — How do you verify extracted data against master data?
3. **Confidence & escalation** — How do you decide when to auto-execute vs. escalate?
4. **Memory & learning** — How does the system get smarter over time?
5. **Governance & audit** — How do you maintain control and compliance?
6. **Integration surface** — How do you connect to existing ERP/TMS systems without requiring adoption?

Each competitor made different bets on these six axes. The goal here is to map those bets, identify the strongest pattern for each axis, and compose a system that cherry-picks the best of each.

## First Principles: What Makes a Supply Chain AI Agent Architecture Good or Bad

Before examining companies, establish the evaluation criteria. A good supply chain AI architecture is one that:

**Handles format chaos gracefully.** The fundamental input to these systems is not clean API data. It is coffee-stained PDFs, inconsistent spreadsheets, free-text emails in multiple languages, and screenshots of SAP screens taken on someone's phone. The architecture must treat format diversity as the default, not the exception. Any system that requires templates or standardized input has already lost.

**Degrades to human action, never to silence.** The worst failure mode is not a wrong answer — it is no answer. When the AI cannot process something, the system must surface it to a human with enough context to act immediately. The escalation path is not an error handler. It is the primary design constraint.

**Learns from corrections without retraining.** Enterprise ML systems that require model retraining to incorporate feedback are dead on arrival. The correction-to-improvement loop must be measured in minutes, not sprint cycles. The best architectures treat human corrections as first-class training data that takes effect immediately.

**Separates confidence from capability.** A system might be perfectly capable of processing an order but have low confidence in a specific extraction. The architecture must distinguish between "I cannot do this" (capability gap) and "I can do this but I am not sure I did it right" (confidence gap). These require fundamentally different escalation paths.

**Maintains a complete audit trail.** In supply chain, every automated decision has financial and contractual implications. The architecture must make every decision explainable after the fact — not just "what did the AI decide" but "why did it decide that, what data did it see, and what alternatives did it consider."

## How It Actually Works: Company-by-Company Engineering Breakdown

### Glacis — The Anti-Portal Email-First Agent

**Core bet:** Meet suppliers and customers where they already are (email). Never ask anyone to change behavior.

**Document processing:** Email-first ingestion. The agent monitors a shared inbox (like `orders@company.com`), pulls emails and attachments, and processes whatever format arrives — free text in the body, PDF attachments, Excel spreadsheets, even XML/EDI. No templates. The system adapts to the sender, not the other way around.

**Validation:** Extracted data is validated against ERP master data — product catalogs, price lists, customer records. The agent maps customer-specific descriptions ("Dark Roast 5lb bag") to internal item codes using fuzzy matching against the product master. Cross-references price, quantity, and delivery dates against the original PO for confirmation workflows.

**Confidence & escalation:** Three-tier output — auto-execute (high confidence), clarify (missing data — agent emails back asking for specifics), escalate (human review with one-click approval). The $10B manufacturer case study shows 93% reduction in processing time, meaning roughly 93% of orders fall into auto-execute.

**Memory:** SOP playbooks encode customer-specific and supplier-specific rules. The agent follows these playbooks for each trading partner. Learning happens through playbook updates after human corrections.

**Key pattern to steal:** The *Anti-Portal Design* — zero adoption barrier as an architectural constraint. See [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design]]. Every design decision flows from "the external party changes nothing."

### Pallet (CoPallet) — Generator-Judge Deep Reasoning with Enterprise Memory

**Core bet:** Multi-model consensus with iterative self-correction. Never trust a single LLM pass.

**Document processing:** LLM-based semantic comprehension rather than traditional OCR. Documents are processed through what they call "digitization actions" — the LLM reads the document as a human would, understanding context and layout rather than extracting text from fixed coordinates. GPU-accelerated instances handle complex document processing while lightweight workers handle simple lookups.

**Validation:** Dual-search retrieval combining semantic search (vector embeddings across document chunks, 8 results) and structured search (filtered queries on entity metadata like customer names and routes, 5 results). The system pulls 13 context items per decision, synthesizing customer preferences, carrier history, and facility-specific constraints.

**Confidence & escalation:** The **Generator-Judge pattern** is Pallet's strongest contribution. A Generator model analyzes the situation and produces an output. A separate Judge model evaluates whether the output meets standards. If the Judge rejects, it provides specific feedback and the Generator retries. After multiple failed iterations, the task escalates to a human. As the system processes more tasks of the same type, iteration counts decrease — the system literally gets faster at things it has done before.

**Memory:** This is Pallet's most differentiated subsystem. SOPs are decomposed into "thousands of discrete memories" stored in plain English with rich metadata (customer names, facility IDs, route designations, topic classifications). The system auto-generates a taxonomy organizing memories by topic, customer, and location. Human corrections are captured as new memories immediately — no retraining cycle. The system can even infer SOPs from historical transaction logs when written documentation does not exist.

**Multi-model consensus:** Workflows run through multiple LLMs (OpenAI, Google Gemini, Anthropic) independently, like having multiple employees compare answers. This is not ensemble averaging — it is consensus verification.

**Observability:** OpenTelemetry tracing on every network request, decision point, and state transition. Full decision replay capability — you can see what the agent "was thinking" at each step.

**Key patterns to steal:** (1) Generator-Judge with iterative feedback. (2) Memory as plain-English discrete facts with semantic + structured dual retrieval. (3) Multi-model consensus for high-stakes decisions. (4) Event-driven async architecture with dynamic compute allocation. See [[Glacis-Agent-Reverse-Engineering-Generator-Judge]] for implementation detail.

### Tradeshift — OCR + LLM Hybrid Extraction with Rule-Based Matching

**Core bet:** Combine traditional OCR (AWS Textract) with LLMs for template-free extraction, then layer deterministic matching rules on top.

**Document processing:** A hybrid extraction engine pairs AWS Textract (OCR) with LLMs for interpretation. Textract handles the character-level extraction from both native PDFs and scanned/image-based documents. The LLM interprets the extracted text, understanding document structure without predefined templates. This is a pragmatic middle ground — OCR handles the reliable low-level extraction, LLM handles the unreliable high-level interpretation.

**Validation:** **Document Matching 2.0** uses configurable matching rules with a notable engineering detail: the matching configuration has a real-time JSON preview that shows the exact production configuration before deployment. The UI dynamically disables unsupported attribute combinations and proactively flags duplicates and integrity violations. This is a pattern worth noting — making the matching logic transparent and testable before it processes real documents.

**Confidence & escalation:** The AI enrichment workflow tracks documents through explicit stages: uploaded, extracting, enriching, completed, or flagged for review. ScanIO provides bounding boxes on the original document with missing data suggestions for human reviewers. An Anomaly Detection Dashboard surfaces statistical outliers — fraud, duplicate payments, process breakdowns.

**Coding engine:** Ada 2.0 replaced statistical models with a Decision Tree algorithm, exposed as a single API call. Backward-compatible, more accurate at the coding-list tier. The shift from statistical to decision-tree is interesting — they traded model flexibility for interpretability and debuggability.

**Key patterns to steal:** (1) OCR + LLM hybrid — let OCR handle what it is good at, let LLMs handle what they are good at. (2) Real-time JSON config preview for matching rules. (3) Anomaly detection as a separate layer from extraction.

### Basware — Policy Engine Autonomy Gates

**Core bet:** Governance first. Every AI action flows through a central policy engine before execution.

**Document processing:** SmartPDF converts machine-readable PDFs to structured data at 97%+ accuracy. The ML model considers text, fonts, lines, and logos as features — a multi-modal approach to layout understanding. SmartCoding handles non-PO invoices by analyzing historical data to recommend coding. The system is trained on 2.2 billion invoices — a dataset advantage that is essentially unreplicable for a startup.

**Validation:** Multi-source matching across invoices, POs, goods receipts, and contracts using "recognition methods that combine association settings and calculation rules." The matching is deterministic with ML-assisted recommendations.

**Confidence & escalation:** This is Basware's standout architecture. Every AI agent action flows through a **central policy engine** with **autonomy gates** — business rules, compliance requirements, and risk thresholds evaluated before any action executes. The policy engine is not a post-hoc audit. It is an inline gate. The agent proposes an action, the policy engine evaluates it against the customer's specific rules, and only then does execution proceed.

Their CEO's framing is precise: "Autonomy without trust is just risk." The architecture enforces that every AI decision is explainable and governed.

**Agents:** AP Business Agent (contextual guidance on invoice handling), AP Data Agent (natural language queries against AP data). Planned: Supplier Agent (automated dispute management), AP Pro Agent (NL interface for processing questions).

**Key patterns to steal:** (1) The **autonomy gate** as an inline architectural component — not just logging decisions, but gating them through configurable policy. (2) Human-in-the-loop where corrections are learned immediately ("once answered, the AI learns immediately, never asking the same question again"). (3) Separating the agent (proposes action) from the policy engine (approves action).

### Esker — Three-Layer AI Stack with Custom Transformer

**Core bet:** Custom domain-specific transformer trained specifically on order processing, not general-purpose LLM.

**Document processing:** A three-layer extraction stack, and this layering is the key insight:
- **Layer 1 — Deep Learning**: Neural network trained on thousands of orders handles first-time documents. This is the cold-start layer.
- **Layer 2 — Machine Learning**: Learns from user corrections transparently, gradually increasing recognition rates. This is the warm-up layer.
- **Layer 3 — Teaching**: Explicit rules defined for recurring orders from known senders. This is the steady-state layer.

The system handles "an infinite number of layouts" and processes at both header and line-item levels.

**Validation:** Automatic validation against ERP reference data (customers, shipping addresses, products). Historical analysis detects unusual quantities and alerts CSRs. Side-by-side comparison of original document with extracted data for human review.

**Confidence & escalation:** Selective "touchless" processing — orders bypass manual intervention when validation succeeds, but the system blocks automatic ERP posting when data checks fail. The architecture prevents false positives (bad orders going through) at the cost of some false negatives (good orders flagged for review).

**Custom model:** Synergy Transformer is a custom-trained language model optimized specifically for order processing. Achieves 92%+ recognition rate. More resource-efficient than general-purpose LLMs for this specific task. The tradeoff: less flexible, but faster and cheaper per inference.

**Key patterns to steal:** (1) The **three-layer extraction stack** — deep learning for cold start, ML for warm-up, explicit rules for steady state. This is the most practical extraction architecture I have seen. (2) Domain-specific fine-tuned transformer vs. general-purpose LLM. (3) Asymmetric error handling — block false positives aggressively, tolerate false negatives.

### Celonis — Process Intelligence as Agent Context

**Core bet:** AI agents are only as good as their operational context. Process mining provides that context.

**Architecture:** Celonis is fundamentally different from the other five companies. They do not process documents or manage orders directly. They build a **living digital twin** of business processes from event logs, then feed that context into AI agents. The architecture has three layers:
- **Data Core**: Ingests from any source, processes billions of records, builds the process graph.
- **Process Intelligence Graph**: Fuses event data with business context (policies, rules, KPIs, roles) into a unified model.
- **Orchestration Engine**: Maps "Action Flows" — automated workflows across SCM, CRM, and custom tools, coordinating human and machine tasks.

**Agent integration:** AgentC is not an agent framework — it is a context API. It feeds process intelligence into agents built on Microsoft Copilot Studio, Amazon Bedrock, IBM watsonx, or CrewAI. The Intelligence API exposes process context, metrics, and recommended actions to any AI platform.

**Procurement application:** For procure-to-pay, the platform auto-corrects price and quantity mismatches, routes non-compliant spend to the right channel, and reduces duplicate invoices. But it does this through process visibility and rule execution, not through document AI.

**Key patterns to steal:** (1) The **Process Intelligence Graph** — a living digital twin that gives agents operational context beyond just the current document. (2) Treating process mining output as *input* to AI agents rather than as a separate analytics product. (3) Object-based process mining for understanding how entities (POs, invoices, shipments) flow through the system.

## Comparison Table

| Dimension | Glacis | Pallet | Tradeshift | Basware | Esker | Celonis |
|-----------|--------|--------|------------|---------|-------|---------|
| **Input handling** | Email-first, any format | LLM semantic comprehension | OCR (Textract) + LLM hybrid | SmartPDF multi-modal | 3-layer stack (DL/ML/rules) | Event logs, not documents |
| **Matching approach** | Fuzzy match vs ERP master | Dual-search (semantic + structured) | Configurable rules with JSON preview | Multi-source deterministic + ML | ERP reference + historical anomaly | Process graph + rule execution |
| **Confidence model** | 3-tier (auto/clarify/escalate) | Generator-Judge iterative | Stage-based pipeline | Autonomy gates (policy engine) | Asymmetric (block false positives) | KPI-driven thresholds |
| **Learning mechanism** | SOP playbook updates | Plain-English memories, immediate | Ada 2.0 decision trees | Immediate single-correction learning | 3-layer warm-up over time | Process graph evolution |
| **Governance** | Human-in-the-loop review | Multi-model consensus + escalation | Anomaly detection dashboard | Central policy engine (strongest) | Touchless gating | Process compliance monitoring |
| **Unique strength** | Zero adoption barrier | Deep reasoning + memory layer | Hybrid extraction + matching config | Autonomy gates + 2.2B invoice dataset | Domain-specific transformer + 3-layer | Process context as agent input |
| **Unique weakness** | Limited public technical detail | Complex multi-model infra | Portal-dependent (supplier network) | Portal-centric (Supplier Portal) | Lower extraction rate (92%) | Does not process documents directly |

## The Tradeoffs

### Centralized AI vs. Distributed Agents

**Centralized** (Basware, Tradeshift): One extraction engine, one matching engine, one policy engine. Simpler to reason about, easier to govern, harder to scale to diverse use cases. Basware processes 2.2 billion invoices through essentially the same pipeline.

**Distributed** (Pallet, Glacis): Multiple specialized agents with distinct responsibilities. More flexible, better at handling diverse workflows, harder to maintain consistency. Pallet runs multiple LLMs in parallel for consensus — powerful but expensive.

**Our choice:** Distributed agents (following Glacis's model and Google ADK's multi-agent design) but with Basware's policy engine pattern as an inline governance layer. Each agent proposes, the policy engine disposes.

### Portal vs. Email vs. API

**Portal** (Coupa, Basware, Tradeshift): Requires the external party to log in and use your interface. Gives you clean structured data. But adoption is terrible — Glacis documents 5% portal adoption at one manufacturer.

**Email** (Glacis, Pallet): Meets the external party where they are. Messy unstructured data but zero adoption barrier. This is the correct default for any system targeting SMEs or diverse supplier bases.

**API/EDI** (Celonis integration): Clean machine-to-machine data. Only works for large, tech-mature trading partners. Not relevant for our hackathon scope.

**Our choice:** Email-first, following Glacis's Anti-Portal principle. The anti-portal constraint is non-negotiable for the Google Solution Challenge use case.

### General LLM vs. Domain-Specific Model

**General LLM** (Pallet, Glacis): Use GPT-4/Gemini/Claude for extraction and reasoning. Flexible, handles novel formats, expensive per inference, occasionally hallucinates.

**Domain-specific** (Esker Synergy Transformer, Tradeshift Ada 2.0): Custom-trained or fine-tuned models for the specific task. Faster, cheaper, more accurate on known patterns, brittle on novel inputs.

**Hybrid** (Tradeshift): OCR for reliable low-level extraction, LLM for unreliable high-level interpretation. Best of both worlds, more complex to maintain.

**Our choice:** Gemini multimodal for extraction (required by Google Solution Challenge), with Esker's three-layer philosophy — Gemini handles cold-start documents, learned patterns handle warm documents, explicit rules handle steady-state known senders. This reduces Gemini API costs over time as more senders become "known."

### One-Shot vs. Iterative Verification

**One-shot** (Tradeshift, Basware, Esker): Extract once, validate against rules, pass or fail. Simple, fast, predictable cost per document.

**Iterative** (Pallet Generator-Judge): Extract, judge, retry if needed, escalate after N failures. More accurate on complex documents, higher latency, variable cost.

**Our choice:** Generator-Judge for high-value orders (over a configurable threshold), one-shot for routine orders. This mirrors how human teams work — you do not double-check a standard reorder, but you absolutely double-check a $500K custom order.

## What Most People Get Wrong

**Mistake 1: Building document AI instead of workflow AI.** The extraction is maybe 30% of the problem. The other 70% is what happens after extraction — validation, matching, exception routing, learning from corrections, maintaining audit trails. Esker understands this (three-layer stack). Basware understands this (policy engine). Companies that demo "look, we extracted the PO number!" and stop there have solved the easy part.

**Mistake 2: Treating confidence as a single number.** "The AI is 87% confident" tells you nothing actionable. Confident about what? The extraction accuracy? The item matching? The price validation? Pallet's approach is better — the Generator-Judge pattern produces structured feedback about *what specifically* the Judge rejected. "Price for line item 3 does not match catalog within 5% tolerance" is actionable. "87% confidence" is not.

**Mistake 3: Ignoring the cold-start problem.** Every system works great on document formats it has seen before. The question is what happens with the first order from a new customer in a new format. Esker's three-layer stack is the most honest architecture here — it explicitly acknowledges that cold-start (Layer 1) is a different problem from warm (Layer 2) from steady-state (Layer 3), and uses different techniques for each.

**Mistake 4: Building the AI without building the escalation UX.** If 20% of orders require human review, the human review interface is not an afterthought — it is a core product surface. Glacis gets this right with one-click approval dashboards. Tradeshift gets this right with ScanIO bounding boxes showing exactly where the AI is uncertain. Any system that dumps exceptions into a spreadsheet or email thread has failed at the most critical user experience.

**Mistake 5: Assuming you need a portal.** The entire portal model for supplier collaboration is broken. Glacis proved it (5% adoption), Pallet proved it (email-first logistics), and every enterprise that tried SAP Ariba for direct materials and watched teams revert to email proved it. The architectural constraint is: external parties will not change their behavior. Design for that reality.

## Connections

- [[Glacis-Agent-Reverse-Engineering-Overview]] — Parent research map
- [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] — How Glacis's Order Intake agent works step-by-step
- [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] — How Glacis's PO Confirmation agent works step-by-step
- [[Glacis-Agent-Reverse-Engineering-Anti-Portal-Design]] — The zero-adoption-barrier constraint explored in depth
- [[Glacis-Agent-Reverse-Engineering-Generator-Judge]] — Pallet's Generator-Judge pattern adapted for our build
- [[Glacis-Agent-Reverse-Engineering-Validation-Pipeline]] — Validation patterns synthesized from all competitors
- [[Glacis-Agent-Reverse-Engineering-Exception-Handling]] — Escalation patterns from Basware's autonomy gates and Glacis's 3-tier model
- [[Glacis-Agent-Reverse-Engineering-Document-Processing]] — Multi-format extraction drawing on Esker's 3-layer and Tradeshift's hybrid approaches
- [[Glacis-Agent-Reverse-Engineering-Learning-Loop]] — How Pallet's memory layer and Esker's warm-up pattern inform our design
- [[Supply-Chain-Pallet-Engineering-Patterns]] — Deeper Pallet analysis from the companion research set
- [[Supply-Chain-Glacis-Analysis]] — Strategic Glacis analysis from the companion research set

## Subtopics for Further Deep Dive

1. **Generator-Judge Implementation for Supply Chain** — Adapting Pallet's pattern to order validation with Gemini as Generator and a rules-based Judge. How many iterations? What are the Judge's evaluation criteria? → [[Glacis-Agent-Reverse-Engineering-Generator-Judge]]
2. **Autonomy Gate / Policy Engine Design** — Basware's central policy engine as an inline governance component. How to implement configurable business rules that gate agent actions before execution. → [[Glacis-Agent-Reverse-Engineering-Exception-Handling]]
3. **Three-Layer Extraction Stack** — Esker's cold-start / warm-up / steady-state model mapped onto Gemini multimodal. When does a document graduate from Layer 1 to Layer 2 to Layer 3? → [[Glacis-Agent-Reverse-Engineering-Document-Processing]]
4. **Enterprise Memory Layer for Supply Chain** — Pallet's plain-English memories with dual retrieval (semantic + structured) adapted for order processing SOPs in Firestore with vector search. → [[Glacis-Agent-Reverse-Engineering-Learning-Loop]]
5. **Multi-Model Consensus for High-Stakes Decisions** — When and how to run multiple LLMs for verification. Cost-benefit analysis. Which decisions warrant consensus? → New subtopic
6. **Anomaly Detection as a Separate Layer** — Tradeshift's approach of separating anomaly detection (fraud, duplicates, process breakdowns) from normal extraction/validation. Statistical methods vs. ML. → New subtopic
7. **Process Intelligence Graph for Agent Context** — Celonis's concept of a living digital twin feeding operational context to agents. Lightweight version for our build using Firestore event logs. → New subtopic

## References

### Glacis
- Glacis, "How AI Automates Order Intake in Supply Chain," December 2025
- Glacis, "AI For PO Confirmation V8," March 2026
- See [[Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] and [[Glacis-Agent-Reverse-Engineering-PO-Confirmation-Agent]] for detailed analysis

### Pallet
- [Engineering AI Agents for the Coffee-Stained World of Logistics](https://www.pallet.com/blog/engineering-ai-agents-for-logistics) — CoPallet architecture, action orchestration, event-driven design, observability
- [How to Build AI Agents with a Truckload of Context](https://www.pallet.com/blog/building-ai-agents-with-context) — Memory layer, dual retrieval, entity resolution
- [Introducing Deep Reasoning for Multi-Step Logistics Automation](https://www.pallet.com/blog/introducing-deep-reasoning) — Generator-Judge pattern, iterative verification
- [What's the Difference Between a Real AI Agent and a ChatGPT Wrapper?](https://www.pallet.com/blog/memory-reasoning-overview) — Memory architecture, multi-model consensus, SOP decomposition

### Tradeshift
- [Spring Release '26: AI-Powered AP Automation & Reporting](https://tradeshift.com/resources/ai/spring-release-2026-ai-powered-ap-automation-reporting/) — OCR + LLM hybrid extraction, Document Matching 2.0, anomaly detection
- [Fall 2025 Release: AI & e-Invoicing Compliance](https://tradeshift.com/resources/ai/fall-2025-release-tradeshift-ai-einvoicing-compliance/) — Ada 2.0 decision tree engine, single-API-call architecture

### Coupa
- [Coupa Launches New AI Agents](https://www.coupa.com/newsroom/coupa-launches-new-ai-agents-to-accelerate-source-to-pay-roi-featuring-autonomous-sourcing-collaboration-and-orchestration/) — Navi agent portfolio, 5 agent types
- [AI-Native Spend Management Platform](https://www.coupa.com/platform/ai/) — $9T dataset, community intelligence, agent ecosystem

### Basware
- [Basware AI & Machine Learning](https://www.basware.com/en/why-basware/advanced-cloud-technology/ai-and-machine-learning/) — SmartPDF, SmartCoding, 2.2B invoice dataset, immediate learning from corrections
- [Basware's AI Agents: From Invoicing to 100% Automated](https://www.artificialintelligence-news.com/news/invoicing-agentic-ai-baswares-ai-agents-from-invoicing-to-100-automated/) — Central policy engine, autonomy gates, agent governance architecture
- [Basware Deploys AI Agents for Invoice Processing](https://www.resultsense.com/news/2026-02-25-basware-deploys-ai-agents-for-invoice-processing) — AP Business Agent, AP Data Agent, planned Supplier Agent

### Esker
- [Esker Announces Synergy Transformer AI](https://www.businesswire.com/news/home/20240910436574/en/Esker-Announces-Innovative-Synergy-Transformer-AI-for-Order-Processing-Automation) — Custom domain-specific transformer, 92%+ recognition
- [Order Data Capture](https://www.esker.com/business-process-solutions/order-cash/customer-service-automation/order-management-automation-system/order-data-capture/) — Three-layer extraction stack (deep learning / ML / teaching), touchless processing, ERP validation

### Celonis
- [Enterprise AI Unleashed: AgentC](https://www.celonis.com/blog/enterprise-ai-unleashed-agentc-lets-companies-develop-agents-in-leading-ai-platforms-powered-with-celonis-process-intelligence) — Process Intelligence Graph, Intelligence API, multi-platform agent integration
- [Process Intelligence: A New Phase for Enterprise AI](https://siliconangle.com/2025/12/12/new-phase-enterprise-ai-process-intelligence-celonis-celosphere/) — Data Core, Orchestration Engine, living digital twin, object-based process mining
- [Celonis Process Intelligence Turns Enterprise AI into ROI](https://siliconangle.com/2026/02/05/celonis-process-intelligence-enterprise-ai-roi-celosphere/) — AgentC suite updates, Orchestration Engine acquisition
