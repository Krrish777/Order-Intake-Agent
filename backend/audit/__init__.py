"""Audit-log package for Track D.

Public surface: AuditEvent (schema), AuditLogger (fail-open emitter).
"""
from backend.audit.logger import AuditLogger
from backend.audit.models import AuditEvent

__all__ = ["AuditEvent", "AuditLogger"]
