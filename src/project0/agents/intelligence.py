"""Intelligence stub. Always echoes. No delegation (non-Manager agents
cannot delegate — enforced in the orchestrator)."""

from __future__ import annotations

from project0.envelope import AgentResult, Envelope


async def intelligence_stub(env: Envelope) -> AgentResult:
    return AgentResult(
        reply_text=f"[intelligence-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
