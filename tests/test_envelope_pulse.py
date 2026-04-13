from project0.envelope import AgentResult, Envelope


def test_envelope_accepts_pulse_source_and_routing_reason():
    env = Envelope(
        id=None,
        ts="2026-04-14T00:00:00Z",
        parent_id=None,
        source="pulse",
        telegram_chat_id=None,
        telegram_msg_id=None,
        received_by_bot=None,
        from_kind="system",
        from_agent=None,
        to_agent="manager",
        body="check_calendar",
        mentions=[],
        routing_reason="pulse",
        payload={"pulse_name": "check_calendar"},
    )
    assert env.routing_reason == "pulse"
    assert env.source == "pulse"
    # Round-trip JSON.
    assert Envelope.from_json(env.to_json()).routing_reason == "pulse"


def test_agent_result_delegation_carries_payload():
    r = AgentResult(
        reply_text=None,
        delegate_to="secretary",
        handoff_text="→ 已让秘书帮你记着",
        delegation_payload={"kind": "reminder_request", "appointment": "牙医"},
    )
    assert r.is_delegation()
    assert r.delegation_payload == {
        "kind": "reminder_request",
        "appointment": "牙医",
    }


def test_agent_result_reply_default_payload_is_none():
    r = AgentResult(
        reply_text="hi", delegate_to=None, handoff_text=None
    )
    assert r.delegation_payload is None
