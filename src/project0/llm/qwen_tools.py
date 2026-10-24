"""Qwen3.5's native text tool protocol: render tools into the system turn,
render tool calls/results back into chat text, and parse ``<tool_call>``
blocks out of replies.

Ollama 0.17.x cannot render or parse tools for the custom-imported GGUF
(it reports only the ``completion`` capability), so LocalProvider speaks
the protocol the model was trained on directly. The instruction block and
turn shapes below are copied from the GGUF's embedded
``tokenizer.chat_template`` (Jinja). See
docs/superpowers/specs/2026-10-24-all-agents-local-llm-design.md.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from project0.llm.tools import ToolCall, ToolResultMsg, ToolSpec

log = logging.getLogger(__name__)

TOOLS_INSTRUCTIONS = (
    "If you choose to call a function ONLY reply in the following format with NO suffix:\n\n"
    "<tool_call>\n"
    "<function=example_function_name>\n"
    "<parameter=example_parameter_1>\n"
    "value_1\n"
    "</parameter>\n"
    "<parameter=example_parameter_2>\n"
    "This is the value for the second parameter\n"
    "that can span\n"
    "multiple lines\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>\n\n"
    "<IMPORTANT>\n"
    "Reminder:\n"
    "- Function calls MUST follow the specified format: an inner <function=...></function> "
    "block must be nested within <tool_call></tool_call> XML tags\n"
    "- Required parameters MUST be specified\n"
    "- You may provide optional reasoning for your function call in natural language "
    "BEFORE the function call, but NOT after\n"
    "- If there is no function call available, answer the question like normal with your "
    "current knowledge and do not tell the user about function calls\n"
    "</IMPORTANT>"
)

_CALL_RE = re.compile(
    r"<tool_call>\s*<function=([^>\s]+)>(.*?)</function>\s*</tool_call>", re.DOTALL
)
_PARAM_RE = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.DOTALL)


def render_tools_system(tools: list[ToolSpec], system_text: str) -> str:
    tool_lines = [
        json.dumps(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            },
            ensure_ascii=False,
        )
        for t in tools
    ]
    out = (
        "# Tools\n\nYou have access to the following functions:\n\n<tools>\n"
        + "\n".join(tool_lines)
        + "\n</tools>\n\n"
        + TOOLS_INSTRUCTIONS
    )
    if system_text.strip():
        out += "\n\n" + system_text.strip()
    return out


def _render_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _render_call(call: ToolCall) -> str:
    params = "".join(
        f"<parameter={k}>\n{_render_value(v)}\n</parameter>\n" for k, v in call.input.items()
    )
    return f"<tool_call>\n<function={call.name}>\n{params}</function>\n</tool_call>"


def render_assistant_turn(text: str | None, tool_calls: list[ToolCall]) -> str:
    out = text or ""
    for i, call in enumerate(tool_calls):
        if i == 0:
            out += ("\n\n" if out.strip() else "") + _render_call(call)
        else:
            out += "\n" + _render_call(call)
    return out


def render_tool_results(results: list[ToolResultMsg]) -> str:
    return "\n".join(
        f"<tool_response>\n{'[tool error] ' if r.is_error else ''}{r.content}\n</tool_response>"
        for r in results
    )


def _schema_type(prop: Any) -> str | None:
    if not isinstance(prop, dict):
        return None
    typ = prop.get("type")
    if isinstance(typ, list):
        for t in typ:
            if t != "null":
                return str(t)
        return None
    return typ if isinstance(typ, str) else None


def _coerce(raw: str, typ: str | None) -> Any:
    try:
        if typ == "integer":
            return int(raw.strip())
        if typ == "number":
            return float(raw.strip())
        if typ == "boolean":
            low = raw.strip().lower()
            if low in ("true", "yes", "1"):
                return True
            if low in ("false", "no", "0"):
                return False
            return raw
        if typ in ("object", "array"):
            return json.loads(raw)
    except ValueError:
        return raw
    return raw


def parse_tool_calls(reply: str, tools: list[ToolSpec]) -> tuple[str | None, list[ToolCall]]:
    """Return (text before the first call or None, parsed calls). A reply
    with no well-formed call comes back as plain text."""
    matches = list(_CALL_RE.finditer(reply))
    if not matches:
        if "<tool_call>" in reply or "<function=" in reply:
            log.warning("local llm: malformed tool call in reply; treating as text")
        stripped = reply.strip()
        return (stripped or None, [])

    props_by_tool = {t.name: (t.input_schema or {}).get("properties") or {} for t in tools}
    calls: list[ToolCall] = []
    for i, m in enumerate(matches, start=1):
        name, body = m.group(1), m.group(2)
        props = props_by_tool.get(name, {})
        args = {
            pm.group(1): _coerce(pm.group(2), _schema_type(props.get(pm.group(1))))
            for pm in _PARAM_RE.finditer(body)
        }
        calls.append(ToolCall(id=f"call_{i}", name=name, input=args))
    prefix = reply[: matches[0].start()].strip()
    return (prefix or None, calls)
