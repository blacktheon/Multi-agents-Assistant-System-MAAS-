"""Central registry of agents and their metadata.

Adding a new agent to the skeleton is exactly:
  1. Write the agent module (an async function returning AgentResult).
  2. Add a row to AGENT_REGISTRY and AGENT_SPECS below.
  3. Add the corresponding TELEGRAM_BOT_TOKEN_* env var.

The orchestrator imports AGENT_REGISTRY to dispatch by name. telegram_io
imports AGENT_SPECS to know which bot token belongs to which agent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project0.agents.intelligence import intelligence_stub
from project0.agents.manager import manager_stub
from project0.envelope import AgentResult, Envelope

AgentFn = Callable[[Envelope], Awaitable[AgentResult]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    token_env_key: str  # which .env variable holds this agent's bot token


AGENT_REGISTRY: dict[str, AgentFn] = {
    "manager": manager_stub,
    "intelligence": intelligence_stub,
}

AGENT_SPECS: dict[str, AgentSpec] = {
    "manager": AgentSpec(name="manager", token_env_key="TELEGRAM_BOT_TOKEN_MANAGER"),
    "intelligence": AgentSpec(
        name="intelligence", token_env_key="TELEGRAM_BOT_TOKEN_INTELLIGENCE"
    ),
}
