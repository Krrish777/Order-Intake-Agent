"""Shared prompt layer — first-class versioned assets separate from tool code.

Each module here exports prompt constants for one tool or agent. Treating
prompts as top-level concerns (not hidden inside tool internals) makes them
discoverable, reviewable in PRs, and easy to migrate to runtime-configurable
storage (e.g. Firestore) later if ops needs to edit without redeploying.
"""
