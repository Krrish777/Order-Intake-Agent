"""Root conftest — test-wide environment setup.

Sets ``FIRESTORE_EMULATOR_HOST`` and ``GOOGLE_CLOUD_PROJECT`` before any
test module is imported. This is load-bearing:
:mod:`backend.my_agent.agent` evaluates
``root_agent = _build_default_root_agent()`` at import time (intentional —
``adk web .`` discovers the root agent by attribute scan), which
constructs an async Firestore client via
:func:`~backend.tools.order_validator.tools.firestore_client.get_async_client`.
That client factory raises :class:`DefaultCredentialsError` without
either real GCP credentials or the emulator env vars.

The ``Makefile``'s ``dev`` target sets these before invoking
``adk web``; this conftest does the same for pytest. No real network is
touched during test collection — the client is lazy on first use and
unit tests never reach that path (they mock the client or use
:class:`FakeAsyncClient` from ``tests/unit/conftest.py``). Integration
tests that DO hit the emulator have their own
``not os.environ.get("FIRESTORE_EMULATOR_HOST")`` skip guard and will
happily use whatever value we set here.

Using :func:`os.environ.setdefault` means a caller who already exported
these (e.g. via ``make test`` or in CI) wins — we only fill in when the
env is empty.
"""

from __future__ import annotations

import os

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "demo-order-intake-local")
