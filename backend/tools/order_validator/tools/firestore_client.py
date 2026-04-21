"""Async Firestore client factory for the validator's master-data tool.

Mirrors :func:`scripts.load_master_data._client` exactly — same default
project id, same ``FIRESTORE_EMULATOR_HOST`` discovery via the SDK — but
returns the async flavour because ADK stages run in an asyncio loop and
the repo's methods are ``async``.
"""

from __future__ import annotations

import os
from typing import Optional

from google.cloud.firestore import AsyncClient

DEFAULT_PROJECT = "demo-order-intake-local"


def get_async_client(project: Optional[str] = None) -> AsyncClient:
    """Return an ``AsyncClient`` wired to the emulator (if
    ``FIRESTORE_EMULATOR_HOST`` is set) or to live Firestore otherwise.

    The project id falls back to ``$GOOGLE_CLOUD_PROJECT`` and then to
    ``demo-order-intake-local`` — matching the seed script so emulator
    runs read and write the same logical database.
    """
    resolved = project or os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_PROJECT)
    return AsyncClient(project=resolved)


__all__ = ["DEFAULT_PROJECT", "get_async_client"]
