---
type: research-design-note
topic: "Order-Intake Agent: Pre-Filter Architecture"
parent: "[[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]]"
date: 2026-04-20
tags:
  - research
  - adk
  - architecture
  - cost-optimization
  - llamaclassify
  - pre-filter
  - guardrails
---

# ADK Pre-Filter Patterns for Order-Intake

> [!info] Context
> This note was written while planning Step 1b (the ADK wrapper for `classify_document`). The user asked: *"before reach to the agent each and every mail we will receive is going to be go through by small model that will decide is this doc's intent. If we push every email to triage agent then it is going to exhaust our token."*
> The research question: **how does ADK support gating inbound traffic BEFORE an expensive agent runs, and what does the LlamaParse platform itself say about this pattern?**

---

## The Problem

Every order-intake deployment has the same inbox shape: the shared `orders@company.com` inbox receives a mix of actual purchase orders, PO confirmations, shipping notices, inquiries, complaints, marketing newsletters, vendor cold-outreach, and spam. The research from [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Order-Intake-Agent|Order Intake]] shows that a $10B manufacturer's shared inbox carries order traffic through 6-8 distinct channels and formats; Pfizer specifically needed a triage step before automation because not every inbound message is an order.

If every email — including the 30-50% that carry no order intent — invokes a full ADK agent run, three things go wrong:

1. **Token waste.** A Flash-backed triage agent spends roughly 500-3000 input tokens per email just to read it and decide "not an order, drop." On a 10K-email/month inbox, that is millions of tokens burned on decisions that don't need an LLM at all.
2. **Observability noise.** Every drop produces an ADK session, a tool-call history, and logs. Finding real processing traces becomes signal-hunting through drops.
3. **Latency cost on the real work.** Even at Flash speeds, an extra agent round-trip adds seconds of end-to-end latency to every order.

The question is where to gate. ADK gives us four places. The right answer depends on what the gate is doing — and `classify_document` is already answering the gate's question as a side effect of work the downstream pipeline needs anyway.

---

## First Principles

The Glacis whitepapers describe Order Intake's Step 1 as a "lightweight classifier (Gemini Flash with a short system prompt)" that triages the inbox. That is the *reference* architecture — a cheap LLM that decides "order" vs "not order" before the heavy extraction runs. Our context is slightly different: we already have to run [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Validation-Pipeline|validation]] and [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Item-Matching|item matching]] downstream, and both need `document_format` (to pick a parse path) and `document_intent` (to know which schema to extract). So classification has to happen somewhere in the pipeline regardless.

The insight: **if classification runs anyway, the marginal cost of using that classification as a gate is zero.** You are not paying for a second pass to filter — the first pass is the filter. A separate Flash-backed triage agent would be a *redundant* LLM pass that re-answers a question `classify_document` already settled.

This reframes the design. The question is no longer "should we gate?" — it is "where in the topology do we put the classification so it gates AND feeds the downstream pipeline with a single call."

That gives four answers — four distinct places ADK lets us intercept.

---

## Four Pre-Filter Patterns in ADK

### Option A — Pre-ADK Python gate

The Gmail → Pub/Sub → Cloud Run subscriber calls `classify_document(content, filename)` as a plain Python function. If `document_intent` is not order-relevant, the subscriber never invokes `runner.run_async()`; it archives / labels the message and returns. Only order-relevant mail enters the ADK Runner, and the classification result rides along as initial session state.

```python
# backend/pipelines/email_ingest.py  (sketch; built in a later step)
_ORDER_RELEVANT = frozenset({"purchase_order", "po_confirmation", "shipping_notice"})

async def handle_incoming_email(envelope: EmailEnvelope) -> None:
    for artifact in envelope.as_artifacts():
        try:
            classified = classify_document(artifact.bytes, artifact.filename)
        except ClassifyError as exc:
            _route_classify_error(artifact, exc)
            continue

        if classified.document_intent not in _ORDER_RELEVANT:
            _log.info("ingress_drop", intent=classified.document_intent)
            _gmail_label(artifact, "auto/non-order")
            continue

        await runner.run_async(
            user_id="ingest",
            session_id=envelope.message_id,
            new_message=_wrap_artifact_as_content(artifact),
            state_delta={"classified_document": classified.model_dump()},
        )
```

**What it costs:** one LlamaClassify FAST call per artifact. No ADK Session, no Runner turn, no Gemini call on drops.

**What it trades:** drops live outside the ADK trace surface. Observability has to come from the structured log sink + Gmail labels + (optionally) a Firestore audit collection.

**When it fits:** inboxes with ≥20-30% non-order traffic. Our data shows ≥30%, so this is the primary fit.

---

### Option B — ADK Plugin with `before_agent_callback`

A `BasePlugin` subclass registered on the `App`. The plugin's `before_agent_callback` fires globally before every agent invocation managed by the Runner. If the incoming message fails the gate, the plugin returns a `types.Content` short-circuit, which ADK docs describe as: *"Return Content to skip the agent's run."* (see [ADK callback types](https://adk.dev/callbacks/types-of-callbacks/index.md))

ADK's own Plugins guide is explicit about when to reach for Plugins over Callbacks: *"When implementing security guardrails and policies, use ADK Plugins for better modularity and flexibility than Callbacks."* ([ADK Plugins](https://adk.dev/plugins/index.md))

**What it costs:** a Session gets created even for drops (overhead: ~50-100ms, zero LLM cost). The Plugin + App + Runner wiring is always in the path.

**What it trades:** drops appear as ADK events — unified audit stream — for a small per-call overhead.

**When it fits:** if every ingress decision needs to be visible in the same trace system as real agent runs, and the business will never bypass the Runner (e.g., future parallel pipelines all share the same gate).

---

### Option C — `before_agent_callback` on the root agent

Attach the same classifier check directly to the root SequentialAgent's `before_agent_callback`. This is the same mechanism as Option B but scoped to one pipeline instead of global.

```python
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

def classify_gate(ctx: CallbackContext) -> types.Content | None:
    classified = classify_document(ctx.state["__raw_bytes"], ctx.state["__filename"])
    ctx.state["classified_document"] = classified.model_dump()
    if classified.document_intent not in _ORDER_RELEVANT:
        return types.Content(
            role="model",
            parts=[types.Part(text=f"halt: intent={classified.document_intent}")],
        )
    return None

root_agent = SequentialAgent(
    name="order_pipeline",
    before_agent_callback=classify_gate,
    sub_agents=[document_processor, validation_agent, exception_handler],
)
```

**What it costs:** same ~50-100ms session overhead as Option B. The callback runs after the Session exists but before any sub-agent fires.

**What it trades:** less cross-cutting than a Plugin — bound to this specific root agent. Good for single-pipeline MVPs.

**When it fits:** MVP where we want the gate in ADK's audit trail without paying the abstraction cost of a Plugin.

---

### Option D — Custom `BaseAgent` router

Implement a `ConditionalRouter(BaseAgent)` whose `_run_async_impl` reads `classified_document` from state (populated by the wrapper tool or an earlier step) and delegates to `document_processor_agent` or yields a halt event. This is the closest analogue to the explicit decision-tree from [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Validation-Pipeline|Validation-Pipeline]] and [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Exception-Handling|Exception-Handling]].

```python
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from typing import AsyncGenerator

class OrderRouter(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        c = ctx.session.state.get("classified_document", {})
        if c.get("document_intent") not in _ORDER_RELEVANT:
            yield Event(author=self.name, actions=EventActions(escalate=True))
            return
        async for event in self.document_processor.run_async(ctx):
            yield event
```

**What it costs:** code to maintain and test. All routing decisions are explicit, so easier to audit; harder to change without a deploy.

**What it trades:** the explicitness of code vs. the flexibility of instructions. Matches the Glacis research, which frames routing as an explicit decision tree (not an LLM reasoning step).

**When it fits:** when routing grows beyond binary drop/forward — separate queues for `purchase_order` vs `po_confirmation` vs `shipping_notice`, or tiered autonomy levels from [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Exception-Handling|Exception Handling]].

---

## Cost Math (Corrected)

A first-principles instinct says "LlamaClassify FAST at 1 credit/page must be cheaper than Gemini Flash." The numbers do not support that at per-call price:

| Path | Unit cost | 10K emails/month |
|---|---|---|
| Gemini 2.5 Flash triage (500-token prompt, 50-token reply) | ~$0.00005 | **~$0.50** |
| LlamaClassify FAST (1 credit/page × $0.00125/credit) | $0.00125 | **~$12.50** |

Per-call, LlamaClassify is ~25x more expensive than a tight Flash prompt. So the "just gate cheaper than agents" framing is wrong.

The correct framing is zero-marginal-cost:

- The pipeline *has* to classify anyway. [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Validation-Pipeline|Validation]] consumes `document_intent` to pick the right schema; [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Item-Matching|item matching]] doesn't run for non-orders; [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-SOP-Playbook|SOP rules]] are scoped by intent and format.
- If we skip the ingress gate and add a Flash triage agent instead, we pay Flash *and* LlamaClassify downstream for the same decision.
- If we gate at ingress with LlamaClassify, we pay it once and reuse the result downstream.

LlamaParse's own pricing page prescribes this directly under *Cost Optimization Strategies*: *"Pre-filter with Classify — Use Classify (1-2 credits/page) to filter documents before running more expensive Parse or Extract jobs."* (`/llamaparse/general/pricing`) — that is the pattern, from the vendor.

---

## The Take

**Primary:** Option A (pre-ADK Python gate in `backend/pipelines/email_ingest.py`). Simplest, aligns with vendor cost guidance, keeps ADK invocations rare and meaningful. Drops are logged + Gmail-labelled, not silent.

**Secondary:** The ADK wrapper tool `classify_document_tool` (Step 1b) still exists and earns its keep *inside* the pipeline. A single `.eml` can carry multiple attachments, each needing independent classification — the document-processor agent loops over them and calls the tool per artifact. Same core `classify_document` function underneath, two call sites.

**Future layering:** Options B/C/D are orthogonal improvements. If observability parity becomes important later, wrap the Python gate in a Plugin (Option B) without changing the tool surface. If routing grows beyond binary drop/forward, graduate to a BaseAgent router (Option D).

---

## What Most People Get Wrong

**"The triage agent should do classification itself."** It should not. The triage agent's job is to route, not to re-derive facts that the pre-filter already computed. A triage agent that re-reads the email to decide intent is paying twice — once for the ingress gate, once for the agent. Either collapse the gate into the triage agent (Option C/D) or skip the triage agent (Option A); don't do both.

**"LlamaClassify FAST is cheaper than Flash."** Per call, it is not. The economic argument for Option A is that classification is load-bearing downstream, not that it is cheap in isolation.

**"Drops should just disappear silently."** Drops are signals. A sudden spike in inquiries or a newsletter flood is a business event — the ingress gate's log entries become the dataset for later SOP refinement ([[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-SOP-Playbook|SOP Playbook]] §Learning Loop). Structured-log every drop with `intent`, `confidence`, and `filename`.

**"Per-email cost is the only axis."** It is not. Latency and audit clarity matter too. Option A is the fastest path for drops (no Session creation). Option B is the best audit story. Pick by what axis you care about most.

---

## Connections

- [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Order-Intake-Agent]] — Step 1 of the Glacis pipeline is the triage/classifier. This note is the ADK implementation of that step.
- [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Validation-Pipeline]] — the downstream consumer of `document_intent`. The 7-check confidence-accumulation pipeline assumes the intent is already known.
- [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Exception-Handling]] — the 3-level autonomy model runs AFTER the pipeline; ingress drops never reach it.
- [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-Item-Matching]] — only runs for order-relevant documents; non-orders skip item matching entirely.
- [[Glacis-Deep-Dive/Glacis-Agent-Reverse-Engineering-SOP-Playbook]] — the threshold and routing rules for "what counts as order-relevant" should eventually move into the playbook rather than being hardcoded in `_ORDER_RELEVANT`.

## References

- [ADK — Types of Callbacks](https://adk.dev/callbacks/types-of-callbacks/index.md) — `before_agent_callback` returning `types.Content` to skip agent execution.
- [ADK — Plugins](https://adk.dev/plugins/index.md) — `BasePlugin`, global callback hooks, security-guardrail guidance.
- [ADK — Callbacks Overview](https://adk.dev/callbacks/index.md) — lifecycle taxonomy and when each callback fires.
- [LlamaParse — Pricing & Cost Optimization](https://developers.llamaindex.ai/llamaparse/general/pricing) — classify rates (FAST 1 credit, Multimodal 2 credits) + the "Pre-filter with Classify" cost rule from the vendor itself.
- [LlamaParse — Classify Overview](https://developers.llamaindex.ai/llamaparse/classify/) — *"Use as a pre-processing step — Before extraction: Classify first, then run schema-specific extraction with different LlamaExtract agents to improve accuracy and reduce cost."*
- Plan file for Step 1 / 1b: `C:\Users\777kr\.claude\plans\here-is-the-thing-playful-squid.md`
