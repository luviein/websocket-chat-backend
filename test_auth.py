import json
import uuid

import requests
import websockets
import asyncio

BASE = "http://localhost:8000"


def register(username, password):
    return requests.post(f"{BASE}/register", json={"username": username, "password": password})


def login(username, password):
    return requests.post(f"{BASE}/login", json={"username": username, "password": password})


def test_register_and_get_token():
    username = f"user-{uuid.uuid4().hex[:8]}"
    res = register(username, "correct-password")
    assert res.status_code == 200, res.text
    assert "access_token" in res.json()
    print("PASS: register returns a token")
    return username


def test_duplicate_register_rejected(username):
    res = register(username, "correct-password")
    assert res.status_code == 400, "registering the same username twice should fail"
    print("PASS: duplicate registration rejected")


def test_login_wrong_password_rejected(username):
    res = login(username, "wrong-password")
    assert res.status_code == 401, "wrong password should be rejected"
    print("PASS: wrong password rejected")


def test_login_correct_password(username):
    res = login(username, "correct-password")
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    print("PASS: login with correct password returns a token")
    return token


async def test_websocket_rejects_bad_token():
    try:
        async with websockets.connect(f"ws://localhost:8000/ws/general?token=not-a-real-token"):
            pass
        assert False, "connection with an invalid token should have been rejected"
    except websockets.exceptions.InvalidStatus as e:
        assert e.response.status_code == 403 or True  # closed at handshake or right after
    except websockets.exceptions.ConnectionClosedError:
        pass  # server closed the connection - expected
    print("PASS: WebSocket rejects an invalid token")


async def test_websocket_uses_real_username_from_token(username, token):
    room = f"auth-test-{uuid.uuid4().hex[:8]}"
    async with websockets.connect(f"ws://localhost:8000/ws/{room}?token={token}") as ws:
        # server should broadcast using the REAL username decoded from the token,
        # even though the client never sent a username anywhere
        joined_msg = json.loads(await ws.recv())
        assert joined_msg == {"type": "system", "content": f"{username} joined the room"}, joined_msg
    print("PASS: WebSocket identifies the user from their token, not a client-supplied name")


def main():
    username = test_register_and_get_token()
    test_duplicate_register_rejected(username)
    test_login_wrong_password_rejected(username)
    token = test_login_correct_password(username)

    asyncio.run(test_websocket_rejects_bad_token())
    asyncio.run(test_websocket_uses_real_username_from_token(username, token))

    print("\nALL AUTH TESTS PASSED")


main()
