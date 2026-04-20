from google.adk.agents.llm_agent import Agent

from backend.utils.logging import get_logger

_log = get_logger(__name__)


def _build_root_agent() -> Agent:
    """Construct the top-level ADK agent.

    Wrapping construction in a function (rather than running it at import time)
    gives us a single hook to log which model/name/description actually got
    wired up — useful when future config makes these env-driven.
    """
    _log.info("agent_build_start", agent_name="root_agent", model="gemini-2.5-flash")
    agent = Agent(
        model="gemini-2.5-flash",
        name="root_agent",
        description="A helpful assistant for user questions.",
        instruction="Answer user questions to the best of your knowledge",
    )
    _log.info("agent_build_complete", agent_name=agent.name, model=agent.model)
    return agent


root_agent = _build_root_agent()
