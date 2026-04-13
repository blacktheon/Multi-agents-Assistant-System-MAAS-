"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Most agents are plain async functions (skeleton stubs). Secretary is a class
instance with dependencies (LLM provider, memory, config), so main.py
constructs it at startup and calls `register_secretary` to install it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.agents.intelligence import intelligence_stub
from project0.agents.manager import manager_stub
from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    "manager": manager_stub,
    "intelligence": intelligence_stub,
    # "secretary" installed by register_secretary(...) in main.py.
}

LISTENER_REGISTRY: dict[str, ListenerFn] = {
    # "secretary" installed by register_secretary(...) in main.py.
}

AGENT_SPECS: dict[str, AgentSpec] = {
    "manager": AgentSpec(name="manager", token_env_key="TELEGRAM_BOT_TOKEN_MANAGER"),
    "intelligence": AgentSpec(
        name="intelligence", token_env_key="TELEGRAM_BOT_TOKEN_INTELLIGENCE"
    ),
    "secretary": AgentSpec(
        name="secretary", token_env_key="TELEGRAM_BOT_TOKEN_SECRETARY"
    ),
}


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's `handle` callable into both registries. Called
    once from main.py after the Secretary instance is constructed. Adapts
    the `AgentResult | None` return type to the `AgentResult` expected by
    AGENT_REGISTRY by returning a visible fallback reply if handle() returns
    None (which only happens on addressed/DM paths when the LLM call fails).

    Secretary is the only agent that can return None (meaning 'observed
    silently'). For AGENT_REGISTRY callers (addressed paths), it never
    returns None in practice except on LLM error — in which case we surface
    a small fail-visible placeholder rather than dropping the turn silently.
    """

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(秘书暂时走神了...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["secretary"] = agent_adapter
    LISTENER_REGISTRY["secretary"] = handle
