from backend.models.exception_record import ExceptionRecord
from backend.models.judge_verdict import JudgeFinding, JudgeFindingKind, JudgeVerdict


def test_exception_record_schema_version_is_4_after_track_b():
    assert ExceptionRecord.model_fields["schema_version"].default == 4


def test_exception_record_judge_verdict_defaults_to_none():
    from tests.unit.test_exception_store import _sample_exception
    record = _sample_exception()
    assert record.judge_verdict is None


def test_exception_record_accepts_populated_judge_verdict():
    from tests.unit.test_exception_store import _sample_exception
    verdict = JudgeVerdict(
        status="rejected",
        reason="clarify body makes an unauthorized commitment",
        findings=[
            JudgeFinding(
                kind=JudgeFindingKind.UNAUTHORIZED_COMMITMENT,
                quote="we will ship immediately",
                explanation="clarify emails ask questions; no ship commitment authorized.",
            )
        ],
    )
    record = _sample_exception().model_copy(update={"judge_verdict": verdict})
    dumped = record.model_dump(mode="json")
    assert dumped["judge_verdict"]["status"] == "rejected"
    assert dumped["judge_verdict"]["findings"][0]["kind"] == "unauthorized_commitment"
