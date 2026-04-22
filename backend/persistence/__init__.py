"""Persistence layer for the order intake pipeline.

Two Firestore-backed stores (orders, exceptions) plus a thin coordinator
that routes :class:`~backend.models.validation_result.ValidationResult`
outputs to the correct store based on
:class:`~backend.models.validation_result.RoutingDecision`.

Public surface:

* :class:`OrderStore` / :class:`ExceptionStore` — Protocol contracts.
* :class:`FirestoreOrderStore` / :class:`FirestoreExceptionStore` — concrete
  async implementations over ``google.cloud.firestore.AsyncClient``.
* :class:`IntakeCoordinator` — orchestrates validator + stores; the
  single entry point Track A calls after parsing an email.
* :class:`ProcessResult` — sum type returned by ``coordinator.process()``.
"""

from __future__ import annotations

from backend.persistence.base import ExceptionStore, OrderStore

__all__ = ["OrderStore", "ExceptionStore"]
