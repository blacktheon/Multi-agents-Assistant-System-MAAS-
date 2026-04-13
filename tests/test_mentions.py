from project0.mentions import parse_mentions

KNOWN = frozenset({"manager", "intelligence"})


def test_parse_mentions_empty():
    assert parse_mentions("hello world", KNOWN) == []


def test_parse_mentions_single():
    assert parse_mentions("hey @intelligence what's up", KNOWN) == ["intelligence"]


def test_parse_mentions_case_insensitive():
    assert parse_mentions("yo @Manager please", KNOWN) == ["manager"]


def test_parse_mentions_multiple_order_preserved():
    assert parse_mentions("@manager then @intelligence finally", KNOWN) == [
        "manager",
        "intelligence",
    ]


def test_parse_mentions_ignores_unknown_handles():
    assert parse_mentions("@random and @intelligence", KNOWN) == ["intelligence"]


def test_parse_mentions_with_bot_suffix():
    # Telegram usernames often end in _bot; we strip it if the stem is known.
    assert parse_mentions("@intelligence_bot ping", KNOWN) == ["intelligence"]


def test_parse_mentions_trailing_punctuation():
    assert parse_mentions("@manager, please help.", KNOWN) == ["manager"]


def test_parse_mentions_real_telegram_username():
    # Real BotFather usernames have project prefixes that the short-form
    # suffix-strip cannot handle. The username mapping resolves them.
    u2a = {"maas_manager_bot": "manager", "maas_intelligence_bot": "intelligence"}
    assert parse_mentions("@MAAS_manager_bot what's up", KNOWN, u2a) == ["manager"]
    assert parse_mentions("@MAAS_Intelligence_bot news?", KNOWN, u2a) == ["intelligence"]


def test_parse_mentions_username_mapping_plus_short_form():
    # The orchestrator's internal handoff messages use short-form @intelligence,
    # and real user messages use the full @MAAS_* form. Both must resolve.
    u2a = {"maas_manager_bot": "manager"}
    assert parse_mentions("→ forwarding to @intelligence", KNOWN, u2a) == ["intelligence"]
    assert parse_mentions("@MAAS_manager_bot ping", KNOWN, u2a) == ["manager"]
