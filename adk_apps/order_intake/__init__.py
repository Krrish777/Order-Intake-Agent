"""`adk web` / `adk run` / `adk eval` discovery entry for the Order Intake Agent.

This package re-exports the assembled ``root_agent`` from
``backend.my_agent.agent`` so ADK's CLI can discover it without also
listing every sibling package under ``backend/`` as a separate agent.

Layout rationale (see ``research/Track-A-Live-Audit-2026-04-22.md`` F1):
``adk web <scan_dir>`` enumerates every sub-directory of ``scan_dir`` as
an agent candidate. If ``scan_dir`` is ``backend/my_agent``, the sidecar
sub-packages ``agents/`` and ``stages/`` appear as bogus agent entries.
If ``scan_dir`` is ``backend/``, every sibling (``ingestion``, ``models``,
``persistence``, ``tools``, ``utils``) appears. Neither is acceptable.

The canonical ADK layout keeps the agent directory self-contained; this
project can't follow that layout without a massive refactor because
``my_agent`` legitimately reaches into ``backend.persistence``,
``backend.tools``, etc. The ``adk_apps/`` scan directory is a minimal
workaround: it contains only this one thin re-export, so
``adk web adk_apps`` shows exactly one agent.

Existing callers of ``backend.my_agent`` (pytest, scripts/smoke_run.py,
tests/integration) are unaffected — this module defers all real work
to the original package.
"""

from __future__ import annotations

# ADK puts ``adk_apps/`` on sys.path when it imports this package.
# ``backend.my_agent.agent`` lives at the REPO ROOT, which is the parent
# of ``adk_apps/``. Bootstrap the repo root onto sys.path so the
# re-export below resolves. Idempotent guard preserves any existing path.
import sys as _sys
from pathlib import Path as _Path

_repo_root = _Path(__file__).resolve().parents[2]
if str(_repo_root) not in _sys.path:
    _sys.path.insert(0, str(_repo_root))

from backend.my_agent.agent import AGENT_VERSION, ROOT_AGENT_NAME, root_agent  # noqa: E402

__all__ = ["AGENT_VERSION", "ROOT_AGENT_NAME", "root_agent"]
