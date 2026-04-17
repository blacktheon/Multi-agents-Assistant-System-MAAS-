"""Central registry of agents, their metadata, and their listener roles.

Two dicts:
  - AGENT_REGISTRY: routing targets (@mention, focus, default_manager,
    direct_dm, manager_delegation). The orchestrator dispatches an envelope
    to exactly one entry here.
  - LISTENER_REGISTRY: passive observers. After the focus target is
    dispatched, the orchestrator fans out a listener_observation envelope
    to every entry here whose name is not already the focus target.

Manager, Secretary, and Intelligence are class instances with dependencies,
installed via ``register_manager`` / ``register_secretary`` /
``register_intelligence`` from main.py at startup."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]
AgentOptionalFn = Callable[[Envelope], Awaitable[AgentResult | None]]
ListenerFn = Callable[[Envelope], Awaitable[AgentResult | None]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str


AGENT_REGISTRY: dict[str, AgentFn] = {
    # "manager" installed by register_manager(...) in main.py.
    # "secretary" installed by register_secretary(...) in main.py.
    # "intelligence" installed by register_intelligence(...) in main.py.
}

LISTENER_REGISTRY: dict[str, ListenerFn] = {
    # "secretary" installed by register_secretary(...) in main.py.
}

# Raw, *un-adapted* optional-return handlers used by ``handle_pulse``. The
# AGENT_REGISTRY adapters convert None → a fail-visible fallback reply so
# that a silent chat turn still produces something the user can see. For
# pulses, None means "nothing urgent, stay silent" — that must propagate
# unchanged, so pulse dispatch bypasses the adapter via this registry.
PULSE_REGISTRY: dict[str, ListenerFn] = {
    # "manager" installed by register_manager(...) in main.py.
}

AGENT_SPECS: dict[str, AgentSpec] = {
    "manager": AgentSpec(name="manager", token_env_key="TELEGRAM_BOT_TOKEN_MANAGER"),
    "intelligence": AgentSpec(
        name="intelligence", token_env_key="TELEGRAM_BOT_TOKEN_INTELLIGENCE"
    ),
    "secretary": AgentSpec(
        name="secretary", token_env_key="TELEGRAM_BOT_TOKEN_SECRETARY"
    ),
    "learning": AgentSpec(name="learning", token_env_key="TELEGRAM_BOT_TOKEN_LEARNING"),
    "supervisor": AgentSpec(
        name="supervisor", token_env_key="TELEGRAM_BOT_TOKEN_SUPERVISOR"
    ),
}


def register_manager(handle: AgentOptionalFn) -> None:
    """Install Manager's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY."""

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
    PULSE_REGISTRY["manager"] = handle


def register_secretary(handle: ListenerFn) -> None:
    """Install Secretary's ``handle`` into both registries."""

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


def register_intelligence(handle: AgentOptionalFn) -> None:
    """Install Intelligence's ``handle`` into AGENT_REGISTRY. Adapts the
    ``AgentResult | None`` return type to the ``AgentResult`` expected by
    AGENT_REGISTRY by surfacing a fail-visible placeholder if handle()
    returns None (which happens on LLM errors or unhandled routing
    reasons)."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(情报暂时不在状态...)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["intelligence"] = agent_adapter


def register_learning(handle: AgentOptionalFn) -> None:
    """Install Learning's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="（书瑶暂时不在呢...）",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["learning"] = agent_adapter
    PULSE_REGISTRY["learning"] = handle


def register_supervisor(handle: AgentOptionalFn) -> None:
    """Install Supervisor's ``handle`` into AGENT_REGISTRY + PULSE_REGISTRY.
    Not added to LISTENER_REGISTRY — 叶霏 does not passively witness group
    chats; she reviews the stored messages log after the fact."""

    async def agent_adapter(env: Envelope) -> AgentResult:
        result = await handle(env)
        if result is None:
            return AgentResult(
                reply_text="(叶霏走神了...等我一下嘛~)",
                delegate_to=None,
                handoff_text=None,
            )
        return result

    AGENT_REGISTRY["supervisor"] = agent_adapter
    PULSE_REGISTRY["supervisor"] = handle
