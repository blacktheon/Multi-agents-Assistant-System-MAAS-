"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Manager and Secretary are class instances with dependencies, installed
via ``register_manager`` / ``register_secretary`` from main.py at startup.
Intelligence is still a plain async stub (until 6d).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.agents.intelligence import intelligence_stub
from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
AgentOptionalFn = Callable[[Envelope], Awaitable[AgentResult | None]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    "intelligence": intelligence_stub,
    # "manager" installed by register_manager(...) in main.py.
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


def register_manager(handle: AgentOptionalFn) -> None:
    """Install Manager's ``handle`` into AGENT_REGISTRY. Adapts the
    ``AgentResult | None`` return type to the ``AgentResult`` expected by
    AGENT_REGISTRY by surfacing a fail-visible placeholder if handle()
    returns None (which happens on unhandled routing reasons or on LLM
    errors during a chat turn)."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(manager 暂时不便回答...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["manager"] = agent_adapter


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's ``handle`` into both registries (unchanged)."""

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
