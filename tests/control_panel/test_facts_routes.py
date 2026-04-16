from fastapi.testclient import TestClient

from project0.store import Store, UserFactsReader


def test_facts_list_empty(client: TestClient) -> None:
    r = client.get("/facts")
    assert r.status_code == 200
    assert "<form" in r.text


def test_facts_add_creates_row_with_human_author(client: TestClient, store: Store) -> None:
    r = client.post(
        "/facts",
        data={"fact_text": "用户喜欢寿司", "topic": "food"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    facts = UserFactsReader("secretary", store.conn).active()
    assert len(facts) == 1
    assert facts[0].fact_text == "用户喜欢寿司"
    assert facts[0].author_agent == "human"
    assert facts[0].topic == "food"


def test_facts_list_shows_added_fact(client: TestClient) -> None:
    client.post("/facts", data={"fact_text": "A", "topic": ""})
    r = client.get("/facts")
    assert "A" in r.text


def test_facts_edit(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "old", "topic": ""})
    fact_id = UserFactsReader("secretary", store.conn).active()[0].id
    client.post(f"/facts/{fact_id}/edit", data={"fact_text": "new", "topic": "t"})
    facts = UserFactsReader("secretary", store.conn).active()
    assert facts[0].fact_text == "new"
    assert facts[0].topic == "t"


def test_facts_deactivate_then_reactivate(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "X", "topic": ""})
    fid = UserFactsReader("secretary", store.conn).active()[0].id

    client.post(f"/facts/{fid}/deactivate")
    assert UserFactsReader("secretary", store.conn).active() == []

    client.post(f"/facts/{fid}/reactivate")
    assert len(UserFactsReader("secretary", store.conn).active()) == 1


def test_facts_delete_removes_row(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "X", "topic": ""})
    fid = UserFactsReader("secretary", store.conn).active()[0].id
    client.post(f"/facts/{fid}/delete")
    assert UserFactsReader("secretary", store.conn).active() == []
    assert UserFactsReader("secretary", store.conn).all_including_inactive() == []


def test_show_inactive_toggle(client: TestClient, store: Store) -> None:
    client.post("/facts", data={"fact_text": "live", "topic": ""})
    client.post("/facts", data={"fact_text": "gone", "topic": ""})
    facts = UserFactsReader("secretary", store.conn).active()
    gone_id = [f for f in facts if f.fact_text == "gone"][0].id
    client.post(f"/facts/{gone_id}/deactivate")

    r_default = client.get("/facts")
    assert "live" in r_default.text
    assert "gone" not in r_default.text

    r_all = client.get("/facts?show_inactive=1")
    assert "live" in r_all.text
    assert "gone" in r_all.text
