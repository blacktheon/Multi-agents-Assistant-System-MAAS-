import json

from project0.envelope import AgentResult, Envelope, RoutingReason


def make_user_envelope() -> Envelope:
    return Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=-100123,
        telegram_msg_id=42,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="hello",
        mentions=[],
        routing_reason="default_manager",
    )


def test_envelope_roundtrip_user_message():
    env = make_user_envelope()
    blob = env.to_json()
    restored = Envelope.from_json(blob)
    assert restored == env


def test_envelope_roundtrip_all_routing_reasons():
    reasons: list[RoutingReason] = [
        "direct_dm",
        "mention",
        "focus",
        "default_manager",
        "manager_delegation",
        "outbound_reply",
    ]
    for reason in reasons:
        env = make_user_envelope()
        env.routing_reason = reason
        blob = env.to_json()
        restored = Envelope.from_json(blob)
        assert restored.routing_reason == reason


def test_envelope_json_is_plain_dict():
    env = make_user_envelope()
    blob = env.to_json()
    parsed = json.loads(blob)
    assert parsed["source"] == "telegram_group"
    assert parsed["from_kind"] == "user"
    assert parsed["routing_reason"] == "default_manager"


def test_agent_result_reply_only():
    result = AgentResult(reply_text="hi", delegate_to=None, handoff_text=None)
    assert result.is_reply()
    assert not result.is_delegation()


def test_agent_result_delegation_requires_handoff_text():
    result = AgentResult(reply_text=None, delegate_to="intelligence", handoff_text="→ forwarding")
    assert result.is_delegation()
    assert not result.is_reply()


def test_agent_result_validation_rejects_both():
    import pytest

    from project0.errors import RoutingError

    with pytest.raises(RoutingError):
        AgentResult(reply_text="hi", delegate_to="intelligence", handoff_text="→")


def test_agent_result_validation_rejects_neither():
    import pytest

    from project0.errors import RoutingError

    with pytest.raises(RoutingError):
        AgentResult(reply_text=None, delegate_to=None, handoff_text=None)


def test_agent_result_validation_rejects_delegation_without_handoff_text():
    import pytest

    from project0.errors import RoutingError

    with pytest.raises(RoutingError):
        AgentResult(reply_text=None, delegate_to="intelligence", handoff_text=None)


def test_envelope_payload_roundtrips() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="internal",
        telegram_chat_id=123,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="agent",
        from_agent="manager",
        to_agent="secretary",
        body="reminder",
        mentions=[],
        routing_reason="manager_delegation",
        payload={"kind": "reminder_request", "appointment": "项目评审", "when": "明天下午3点"},
    )
    blob = env.to_json()
    roundtripped = Envelope.from_json(blob)
    assert roundtripped.payload == {
        "kind": "reminder_request",
        "appointment": "项目评审",
        "when": "明天下午3点",
    }


def test_envelope_payload_defaults_to_none() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=None,
        source="telegram_group",
        telegram_chat_id=123,
        telegram_msg_id=456,
        received_by_bot="manager",
        from_kind="user",
        from_agent=None,
        to_agent="manager",
        body="hi",
        routing_reason="default_manager",
    )
    assert env.payload is None
    # ensure payload survives a roundtrip even when None
    assert Envelope.from_json(env.to_json()).payload is None


def test_envelope_listener_observation_routing_reason() -> None:
    env = Envelope(
        id=None,
        ts="2026-04-13T12:00:00Z",
        parent_id=1,
        source="internal",
        telegram_chat_id=123,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="secretary",
        body="hi everyone",
        routing_reason="listener_observation",
    )
    roundtripped = Envelope.from_json(env.to_json())
    assert roundtripped.routing_reason == "listener_observation"
