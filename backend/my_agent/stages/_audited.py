"""AuditedStage mixin — wraps _run_async_impl with entry/exit emits.

Subclass contract:
- Class attribute ``name: str`` must be set (stage's canonical name).
- Subclasses implement ``_audited_run(ctx)`` as the real stage body;
  yield Events inside it exactly as you would in ``_run_async_impl``.
- ``correlation_id`` must be present in ``ctx.session.state`` by the
  time a non-Ingest stage runs — IngestStage seeds it as its first
  business-logic act (see Task 5).

The mixin emits ``stage_entered`` BEFORE yielding to ``_audited_run``,
then ``stage_exited`` in a ``finally`` block. If the body raises, the
exit event carries ``outcome=f"error:{ExceptionClass}"`` and the
exception re-raises.

Spec: docs/superpowers/specs/2026-04-24-track-d-audit-log-design.md
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.agents import BaseAgent
from pydantic import PrivateAttr


class AuditedStage(BaseAgent):
    _audit_logger: Any = PrivateAttr()

    def __init__(self, *, audit_logger: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._audit_logger = audit_logger

    async def _run_async_impl(self, ctx):  # type: ignore[override]
        state = ctx.session.state
        correlation_id: str = state.get("correlation_id", "")
        session_id: str = ctx.session.id
        source_message_id = self._extract_source_message_id(state)

        await self._audit_logger.emit(
            correlation_id=correlation_id,
            session_id=session_id,
            source_message_id=source_message_id,
            stage=self.name,
            phase="entered",
            action="stage_entered",
        )

        outcome = "ok"
        try:
            async for event in self._audited_run(ctx):
                yield event
        except BaseException as exc:
            outcome = f"error:{type(exc).__name__}"
            raise
        finally:
            # Re-read state in case _audited_run seeded envelope /
            # correlation_id (IngestStage does both).
            state = ctx.session.state
            correlation_id = state.get("correlation_id", "")
            source_message_id = self._extract_source_message_id(state)
            await self._audit_logger.emit(
                correlation_id=correlation_id,
                session_id=session_id,
                source_message_id=source_message_id,
                stage=self.name,
                phase="exited",
                action="stage_exited",
                outcome=outcome,
            )

    async def _audited_run(self, ctx):  # pragma: no cover
        raise NotImplementedError(
            "AuditedStage subclasses must implement _audited_run"
        )
        yield  # keep async-generator for typing

    @staticmethod
    def _extract_source_message_id(state) -> Optional[str]:
        envelope = state.get("envelope")
        if isinstance(envelope, dict):
            return envelope.get("message_id")
        return None


__all__ = ["AuditedStage"]
