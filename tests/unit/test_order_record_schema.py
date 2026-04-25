from backend.models.order_record import OrderRecord
from backend.models.judge_verdict import (
    JudgeFinding,
    JudgeFindingKind,
    JudgeVerdict,
)


def test_order_record_schema_version_is_5_after_track_b():
    # Track B bumps v4 -> v5 by adding judge_verdict.
    assert OrderRecord.model_fields["schema_version"].default == 5


def test_order_record_judge_verdict_defaults_to_none():
    # Make a minimal record via the same shape the coordinator uses;
    # if the existing file has a _sample_order() helper, import and use it.
    # Otherwise reuse test_order_store's _sample_order via a direct import.
    from tests.unit.test_order_store import _sample_order   # existing helper
    record = _sample_order()
    assert record.judge_verdict is None


def test_order_record_accepts_populated_judge_verdict():
    from tests.unit.test_order_store import _sample_order
    verdict = JudgeVerdict(
        status="rejected",
        reason="hallucinated total",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.HALLUCINATED_FACT,
                quote="$999.99",
                explanation="order.total is 127.40",
            )
        ],
    )
    record = _sample_order().model_copy(update={"judge_verdict": verdict})
    dumped = record.model_dump(mode="json")
    assert dumped["judge_verdict"]["status"] == "rejected"
    assert dumped["judge_verdict"]["findings"][0]["kind"] == "hallucinated_fact"
