import json
import uuid

import pytest
import requests
import websockets

from test_helpers import ws_url


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """A throwaway database for tests that call database.py's functions
    directly, in-process - separate from the `server` fixture's subprocess
    (and its own throwaway DB), and never the real local dev chat.db."""
    import database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "unit_test.db")
    database.init_db()
    return database


def test_display_name_collision_across_account_types_rejected(isolated_db):
    # a password account's username doubles as its display name
    assert isolated_db.create_user("alice", "hashed-pw") is True
    # a Google account choosing "alice" as its display name would collide
    # with that existing password account - DMs/blocking need this to be
    # impossible, since both address people by display name
    assert isolated_db.create_google_user("alice@gmail.com", "google-id-1", "alice") is False
    # a distinct display name is fine
    assert isolated_db.create_google_user("bob@gmail.com", "google-id-2", "bob") is True
    # and a new password registration can't take "bob" either, now that a
    # Google account is already displaying as "bob"
    assert isolated_db.create_user("bob", "hashed-pw-2") is False


def register(server, username, password):
    return requests.post(f"{server}/register", json={"username": username, "password": password})


def login(server, username, password):
    return requests.post(f"{server}/login", json={"username": username, "password": password})


def test_register_returns_token(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    res = register(server, username, "correct-password")
    assert res.status_code == 200, res.text
    assert "access_token" in res.json()


def test_duplicate_registration_rejected(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    register(server, username, "correct-password")  # first registration succeeds
    res = register(server, username, "correct-password")  # second should fail
    assert res.status_code == 400, "registering the same username twice should fail"


def test_wrong_password_rejected(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    register(server, username, "correct-password")
    res = login(server, username, "wrong-password")
    assert res.status_code == 401, "wrong password should be rejected"


def test_correct_password_returns_token(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    register(server, username, "correct-password")
    res = login(server, username, "correct-password")
    assert res.status_code == 200, res.text
    assert "access_token" in res.json()


def test_password_too_short_rejected(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    res = register(server, username, "short")  # under the 8-char minimum
    assert res.status_code == 422, "passwords under the minimum length should be rejected"


@pytest.mark.asyncio
async def test_websocket_rejects_invalid_token(server):
    ws_endpoint = server.replace("http", "ws") + "/ws/general?token=not-a-real-token"
    try:
        async with websockets.connect(ws_endpoint):
            pass
        assert False, "connection with an invalid token should have been rejected"
    except (websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosedError):
        pass  # rejected at handshake or immediately after - both are correct


@pytest.mark.asyncio
async def test_websocket_uses_real_username_from_token(server):
    username = f"user-{uuid.uuid4().hex[:8]}"
    register(server, username, "correct-password")
    token = login(server, username, "correct-password").json()["access_token"]

    room = f"auth-test-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(ws_url(server, room, token)) as ws:
        await ws.recv()  # self
        await ws.recv()  # block_list
        # server should broadcast using the REAL username decoded from the token,
        # even though the client never sent a username anywhere
        joined_msg = json.loads(await ws.recv())
        assert joined_msg == {"type": "system", "content": f"{username} joined the room"}, joined_msg
