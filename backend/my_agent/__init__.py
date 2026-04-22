"""Order Intake Agent — ADK discovery entry point.

When loaded via pytest / other Python tooling, `backend.my_agent` imports
normally: the repo root is already on sys.path (via pytest's rootdir
detection), so cross-package imports like `backend.persistence.*` and
`backend.tools.*` resolve.

When loaded via ADK (`adk run` / `adk web` / `adk eval`), the loader at
`google/adk/cli/utils/agent_loader.py:254` puts the *parent* directory of
the agent package on sys.path (`backend/`) and imports this package as
top-level `my_agent`. Our code reaches outside the agent package to its
siblings (`backend.persistence`, `backend.tools`, etc.), which requires
the *repo root* on sys.path so the `backend` package itself is importable.

We bootstrap that here — idempotent and guarded so pytest's existing
rootdir insertion stays intact.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_repo_root = _Path(__file__).resolve().parents[2]
if str(_repo_root) not in _sys.path:
    _sys.path.insert(0, str(_repo_root))

from . import agent  # noqa: E402

__all__ = ["agent"]
