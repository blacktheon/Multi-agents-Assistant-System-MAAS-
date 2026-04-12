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
