"""Shared types for LLM tool-use conversations.

These types are imported by both the provider layer (which translates them
to/from the Anthropic SDK's wire format) and by agents that use tools
(Manager in 6c). Keeping them in a dedicated module avoids a circular
import between agents and the provider module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolSpec:
    """One tool advertised to the model. ``input_schema`` is a JSONSchema
    dict, passed straight through to Anthropic's ``tools`` parameter."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool_use block emitted by the model. ``id`` is the Anthropic
    tool_use id — required for tool_result pairing on the next turn."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolUseResult:
    """One completion from ``complete_with_tools``. Either the model
    emitted final text (``kind='text'``) or it requested tool calls
    (``kind='tool_use'``). ``text`` may be set in the tool_use variant
    too, carrying optional assistant preamble text."""
    kind: Literal["text", "tool_use"]
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class AssistantToolUseMsg:
    """An assistant turn that called tools. Used when feeding the turn
    back into a follow-up ``complete_with_tools`` call."""
    tool_calls: list[ToolCall]
    text: str | None = None


@dataclass
class ToolResultMsg:
    """A tool_result turn fed back to the model after executing a tool."""
    tool_use_id: str
    content: str
    is_error: bool = False
