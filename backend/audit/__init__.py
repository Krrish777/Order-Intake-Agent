"""Audit-log package for Track D.

Public surface: AuditEvent (schema), AuditLogger (fail-open emitter).
"""
from backend.audit.models import AuditEvent

__all__ = ["AuditEvent"]  # AuditLogger re-exported in Task 2
