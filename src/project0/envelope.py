"""The in-memory and on-disk shape of every message the orchestrator routes.

An Envelope is:
  1. The payload handed to an agent stub.
  2. The row written to messages.envelope_json.
  3. The object reconstructed by the future WebUI to render traces.

It is a plain dataclass on purpose — pydantic is used only for fake Telegram
update models in tests. Serialization goes through explicit to_json/from_json
helpers so that field additions in later sub-projects are unambiguous.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from project0.errors import RoutingError

Source = Literal["telegram_group", "telegram_dm", "internal"]
FromKind = Literal["user", "agent", "system"]
RoutingReason = Literal[
    "direct_dm",
    "mention",
    "focus",
    "default_manager",
    "manager_delegation",
    "outbound_reply",
    "listener_observation",
]


@dataclass
class Envelope:
    id: int | None
    ts: str
    parent_id: int | None
    source: Source
    telegram_chat_id: int | None
    telegram_msg_id: int | None
    received_by_bot: str | None
    from_kind: FromKind
    from_agent: str | None
    to_agent: str
    body: str
    mentions: list[str] = field(default_factory=list)
    routing_reason: RoutingReason = "default_manager"
    payload: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> Envelope:
        data: dict[str, Any] = json.loads(blob)
        return cls(**data)


@dataclass
class AgentResult:
    reply_text: str | None
    delegate_to: str | None
    handoff_text: str | None

    def __post_init__(self) -> None:
        has_reply = self.reply_text is not None
        has_delegate = self.delegate_to is not None
        if has_reply == has_delegate:
            raise RoutingError(
                "AgentResult must set exactly one of reply_text or delegate_to; "
                f"got reply_text={self.reply_text!r}, delegate_to={self.delegate_to!r}"
            )
        if has_delegate and not self.handoff_text:
            raise RoutingError(
                "AgentResult with delegate_to must also set handoff_text"
            )

    def is_reply(self) -> bool:
        return self.reply_text is not None

    def is_delegation(self) -> bool:
        return self.delegate_to is not None
