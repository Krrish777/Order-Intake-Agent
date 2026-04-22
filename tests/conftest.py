"""Root conftest — intentionally minimal.

Historical note: this module used to call
``os.environ.setdefault("FIRESTORE_EMULATOR_HOST", ...)`` and
``os.environ.setdefault("GOOGLE_CLOUD_PROJECT", ...)`` so that importing
``backend.my_agent.agent`` at unit-test collection time would not blow
up on missing GCP credentials — ``root_agent = _build_default_root_agent()``
runs at import time and constructs an async Firestore client via
:func:`backend.tools.order_validator.tools.firestore_client.get_async_client`.

That setdefault pair was moved to :mod:`tests.unit.conftest` (scope:
unit suite only) so integration tests retain their original
skip-or-fail-on-missing-emulator semantic: they must see
``FIRESTORE_EMULATOR_HOST`` set by the user / CI / Makefile, not
implicitly inherited from a root-level pytest hook.
"""

from __future__ import annotations
