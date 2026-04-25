"""SendStage - 10th BaseAgent. Sends Gmail replies for AUTO_APPROVE
confirmation bodies and CLARIFY clarification bodies.

Walks state["process_results"] after FinalizeStage, fail-open per entry.
Subclasses AuditedStage (Track D) so stage entry/exit + lifecycle emits
happen uniformly.

Spec: docs/superpowers/specs/2026-04-24-track-a2-gmail-egress-design.md
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final, Optional

from pydantic import PrivateAttr

from backend.gmail.client import GmailClient
from backend.my_agent.stages._audited import AuditedStage
from backend.persistence.base import ExceptionStore, OrderStore
from backend.utils.logging import get_logger

_log = get_logger(__name__)

SEND_STAGE_NAME: Final[str] = "send_stage"


class SendStage(AuditedStage):
    """Send confirmation + clarify replies via Gmail; record receipts on records.

    Per ProcessResult entry: orders get confirmation_body emails,
    exceptions get clarify_body emails. Recipient resolution: orders
    use customer.contact_email; exceptions fall back to the envelope's
    from_addr (their record carries no recipient).

    Fail-open: per-message exceptions are logged + persisted to
    `send_error` on the record, then processing continues. SIGINT /
    pipeline shutdown is the only way out mid-batch.
    """

    name: str = SEND_STAGE_NAME

    _gmail_client: Optional[Any] = PrivateAttr()
    _order_store: Any = PrivateAttr()
    _exception_store: Any = PrivateAttr()
    _dry_run: bool = PrivateAttr()

    def __init__(
        self,
        *,
        gmail_client: Optional[GmailClient],
        order_store: OrderStore,
        exception_store: ExceptionStore,
        dry_run: bool,
        audit_logger: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(audit_logger=audit_logger, **kwargs)
        self._gmail_client = gmail_client
        self._order_store = order_store
        self._exception_store = exception_store
        self._dry_run = dry_run

    async def _audited_run(self, ctx):
        state = ctx.session.state

        if state.get("reply_handled"):
            if False:
                yield None  # pragma: no cover
            return

        if self._gmail_client is None:
            _log.info("send_stage_disabled", reason="no_gmail_client")
            if False:
                yield None  # pragma: no cover
            return

        envelope = state.get("envelope") or {}
        original_message_id = envelope.get("message_id")
        original_references = envelope.get("references") or []
        original_subject = envelope.get("subject") or ""
        # EmailEnvelope serialises sender field as `from_addr`.
        original_sender = envelope.get("from_addr") or ""

        references_chain: list[str] = list(original_references)
        if original_message_id:
            references_chain.append(original_message_id)

        for entry in state.get("process_results", []):
            result = entry.get("result") or {}
            kind = result.get("kind")

            if kind == "order":
                await self._maybe_send_confirmation(
                    ctx=ctx,
                    order=result.get("order"),
                    original_message_id=original_message_id,
                    references=references_chain,
                    original_subject=original_subject,
                )
            elif kind == "exception":
                await self._maybe_send_clarify(
                    ctx=ctx,
                    exception=result.get("exception"),
                    original_message_id=original_message_id,
                    references=references_chain,
                    original_subject=original_subject,
                    fallback_recipient=original_sender,
                )
            # kind == "duplicate": no new body, nothing to send

        # Keep generator async-iterable
        if False:
            yield None  # pragma: no cover

    async def _maybe_send_confirmation(
        self,
        *,
        ctx,
        order: Optional[dict[str, Any]],
        original_message_id: Optional[str],
        references: list[str],
        original_subject: str,
    ) -> None:
        if order is None:
            return
        source_message_id = order.get("source_message_id") or ""
        body = order.get("confirmation_body")
        if not body:
            await self._emit_skipped(ctx, source_message_id, "no_body")
            return
        if order.get("sent_at") is not None:
            await self._emit_skipped(ctx, source_message_id, "already_sent")
            return
        recipient = ((order.get("customer") or {}).get("contact_email")) or ""
        if not recipient:
            await self._record_failure(source_message_id, self._order_store, "no_recipient")
            await self._emit_failure(ctx, source_message_id, "no_recipient")
            return

        if self._dry_run:
            _log.info(
                "send_dry_run",
                order_id=source_message_id,
                to=recipient,
                subject=original_subject,
            )
            await self._emit_dry_run(ctx, source_message_id, recipient)
            return

        try:
            gmail_id = await asyncio.to_thread(
                self._gmail_client.send_message,
                to=recipient,
                subject=original_subject or "Your order confirmation",
                body_text=body,
                in_reply_to=original_message_id,
                references=references,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _log.error("send_failed", order_id=source_message_id, error=reason)
            await self._record_failure(source_message_id, self._order_store, reason)
            await self._emit_failure(ctx, source_message_id, reason)
            return

        try:
            await self._order_store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=datetime.now(timezone.utc),
                send_error=None,
            )
        except Exception as exc:
            _log.error(
                "send_receipt_write_failed",
                order_id=source_message_id,
                error=str(exc),
            )
        await self._emit_success(ctx, source_message_id, gmail_id)

    async def _maybe_send_clarify(
        self,
        *,
        ctx,
        exception: Optional[dict[str, Any]],
        original_message_id: Optional[str],
        references: list[str],
        original_subject: str,
        fallback_recipient: str,
    ) -> None:
        if exception is None:
            return
        source_message_id = exception.get("source_message_id") or ""
        body = exception.get("clarify_body")
        if not body:
            await self._emit_skipped(ctx, source_message_id, "no_body")
            return
        if exception.get("sent_at") is not None:
            await self._emit_skipped(ctx, source_message_id, "already_sent")
            return

        # ExceptionRecord carries no contact_email; reply to the
        # envelope's original sender. (Track A's clarify-reply path
        # correlates by thread_id, so reply-to-sender threads correctly.)
        recipient = fallback_recipient or ""
        if not recipient:
            await self._record_failure(
                source_message_id, self._exception_store, "no_recipient"
            )
            await self._emit_failure(ctx, source_message_id, "no_recipient")
            return

        if self._dry_run:
            _log.info(
                "send_dry_run",
                exception_id=source_message_id,
                to=recipient,
                subject=original_subject,
            )
            await self._emit_dry_run(ctx, source_message_id, recipient)
            return

        try:
            gmail_id = await asyncio.to_thread(
                self._gmail_client.send_message,
                to=recipient,
                subject=original_subject or "We need a bit more detail",
                body_text=body,
                in_reply_to=original_message_id,
                references=references,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _log.error("send_failed", exception_id=source_message_id, error=reason)
            await self._record_failure(source_message_id, self._exception_store, reason)
            await self._emit_failure(ctx, source_message_id, reason)
            return

        try:
            await self._exception_store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=datetime.now(timezone.utc),
                send_error=None,
            )
        except Exception as exc:
            _log.error(
                "send_receipt_write_failed",
                exception_id=source_message_id,
                error=str(exc),
            )
        await self._emit_success(ctx, source_message_id, gmail_id)

    async def _record_failure(self, source_message_id, store, reason):
        try:
            await store.update_with_send_receipt(
                source_message_id=source_message_id,
                sent_at=None,
                send_error=reason,
            )
        except Exception as exc:
            _log.error("send_receipt_write_failed", error=str(exc))

    async def _emit_success(self, ctx, source_message_id, gmail_id):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_sent",
            outcome="ok",
            payload={"gmail_message_id": gmail_id, "record_id": source_message_id},
        )

    async def _emit_failure(self, ctx, source_message_id, error):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_failed",
            outcome="error",
            payload={"record_id": source_message_id, "error": error},
        )

    async def _emit_dry_run(self, ctx, source_message_id, recipient):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_dry_run",
            outcome="ok",
            payload={"record_id": source_message_id, "would_send_to": recipient},
        )

    async def _emit_skipped(self, ctx, source_message_id, reason):
        await self._audit_logger.emit(
            correlation_id=ctx.session.state.get("correlation_id", ""),
            session_id=ctx.session.id,
            source_message_id=source_message_id,
            stage="lifecycle",
            phase="lifecycle",
            action="email_send_skipped",
            outcome="skip",
            payload={"record_id": source_message_id, "reason": reason},
        )


__all__ = ["SEND_STAGE_NAME", "SendStage"]
