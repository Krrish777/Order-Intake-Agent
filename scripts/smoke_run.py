"""Programmatic live smoke test for the assembled root_agent.

Drives the real 8-stage pipeline via ADK's Runner against live LlamaCloud +
live Gemini + the local Firestore emulator. Emits a per-stage event summary
to stdout and prints the final run_summary + any OrderRecord docs persisted.

Usage:
    uv run python scripts/smoke_run.py data/pdf/patterson_po-28491.wrapper.eml

Prereqs (same as `adk run` — see backend/my_agent/.env):
    - Firestore emulator running (`firebase emulators:start`)
    - Master data seeded (`uv run python scripts/load_master_data.py`)
    - GOOGLE_API_KEY, LLAMA_CLOUD_API_KEY, FIRESTORE_EMULATOR_HOST in env
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.my_agent.agent import AGENT_VERSION, root_agent


async def main(fixture_path: str) -> int:
    print(f"--- smoke run: {fixture_path} (agent_version={AGENT_VERSION}) ---")
    print(f"FIRESTORE_EMULATOR_HOST={os.environ.get('FIRESTORE_EMULATOR_HOST', '(unset)')}")
    print(f"GOOGLE_API_KEY set: {bool(os.environ.get('GOOGLE_API_KEY'))}")
    print(f"LLAMA_CLOUD_API_KEY set: {bool(os.environ.get('LLAMA_CLOUD_API_KEY'))}")

    session_service = InMemorySessionService()
    session_id = f"smoke-{uuid.uuid4().hex[:8]}"
    await session_service.create_session(
        app_name="order_intake_smoke",
        user_id="smoke-user",
        session_id=session_id,
    )
    runner = Runner(
        agent=root_agent,
        app_name="order_intake_smoke",
        session_service=session_service,
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part.from_text(text=fixture_path)],
    )

    events: list = []
    async for event in runner.run_async(
        user_id="smoke-user",
        session_id=session_id,
        new_message=user_msg,
    ):
        events.append(event)
        author = getattr(event, "author", "?")
        state_keys = sorted((event.actions.state_delta or {}).keys()) if event.actions else []
        content_text = ""
        if event.content and event.content.parts:
            parts = [p.text for p in event.content.parts if p.text]
            content_text = " | ".join(parts)[:120]
        print(f"  [{author}] state_delta={state_keys} content={content_text!r}")

    print(f"\n--- {len(events)} events total ---")

    final_session = await session_service.get_session(
        app_name="order_intake_smoke",
        user_id="smoke-user",
        session_id=session_id,
    )
    state = final_session.state if final_session else {}
    print("\n--- final session.state keys ---")
    for k in sorted(state.keys()):
        v = state[k]
        preview = repr(v)[:160]
        print(f"  {k!s:<30} = {preview}")

    run_summary = state.get("run_summary")
    if run_summary:
        print(f"\n--- run_summary ---\n  {run_summary}")
    else:
        print("\n--- NO run_summary on state (something upstream short-circuited) ---")

    process_results = state.get("process_results", [])
    print(f"\n--- process_results ({len(process_results)}) ---")
    for pr in process_results:
        # ASCII arrow — Windows cp1252 stdout can't encode U+2192 ("→").
        print(f"  {pr.get('filename')}#{pr.get('sub_doc_index')} -> {pr.get('result', {}).get('kind')}")

    skipped = state.get("skipped_docs", [])
    if skipped:
        print(f"\n--- skipped_docs ({len(skipped)}) ---")
        for s in skipped:
            print(f"  {s}")

    return 0 if run_summary else 1


if __name__ == "__main__":
    fixture = sys.argv[1] if len(sys.argv) > 1 else "data/pdf/patterson_po-28491.wrapper.eml"
    raise SystemExit(asyncio.run(main(fixture)))
