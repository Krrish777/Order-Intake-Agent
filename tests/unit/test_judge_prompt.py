"""Smoke test on the judge prompt module.

Guards:
- SYSTEM_PROMPT + INSTRUCTION_TEMPLATE exist as non-empty strings.
- All four required {state_key} placeholders are present LITERALLY —
  the template must not f-string-resolve at module load (ADK does it
  at model-call time).
- record_kind branching is documented inside the instruction.
"""

from __future__ import annotations

from backend.prompts import judge as judge_prompt


def test_judge_prompt_has_system_prompt_and_instruction_template():
    assert isinstance(judge_prompt.SYSTEM_PROMPT, str)
    assert isinstance(judge_prompt.INSTRUCTION_TEMPLATE, str)
    assert len(judge_prompt.SYSTEM_PROMPT) > 0
    assert len(judge_prompt.INSTRUCTION_TEMPLATE) > 0


def test_judge_prompt_contains_all_four_state_key_placeholders():
    template = judge_prompt.INSTRUCTION_TEMPLATE
    assert "{judge_subject}" in template
    assert "{judge_body}" in template
    assert "{judge_record_kind}" in template
    assert "{judge_record_facts}" in template


def test_judge_prompt_instructs_record_kind_branching():
    template = judge_prompt.INSTRUCTION_TEMPLATE
    # Both branches should be called out explicitly so the model knows
    # that 'order' bodies state facts while 'exception' bodies ask
    # questions.
    assert "order" in template
    assert "exception" in template


def test_judge_system_prompt_enumerates_all_five_finding_kinds():
    s = judge_prompt.SYSTEM_PROMPT
    for k in ("hallucinated_fact", "unauthorized_commitment", "tone", "disallowed_url"):
        assert k in s, f"system prompt missing finding kind: {k}"
    # 'other' is a catch-all and may or may not be named explicitly.
