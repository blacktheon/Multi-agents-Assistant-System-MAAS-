"""Manager stub.

The stub exists only to exercise the routing contract. Its 'intelligence'
is a single hardcoded rule: if the user's message contains the substring
'news' (case-insensitive), delegate to Intelligence; otherwise echo.

Real routing will be an LLM tool-use call introduced in sub-project 6b.
"""

from __future__ import annotations

from project0.envelope import AgentResult, Envelope


async def manager_stub(env: Envelope) -> AgentResult:
    if "news" in env.body.lower():
        return AgentResult(
            reply_text=None,
            delegate_to="intelligence",
            handoff_text="→ forwarding to @intelligence",
        )
    return AgentResult(
        reply_text=f"[manager-stub] acknowledged: {env.body}",
        delegate_to=None,
        handoff_text=None,
    )
